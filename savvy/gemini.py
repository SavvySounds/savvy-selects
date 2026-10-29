"""Just enough of the Gemini API to have it watch one video: upload, ask, delete.

Standard library on purpose. The official SDK would be a fifth dependency for a
handful of HTTP calls, and new dependencies have to earn their place here.
"""
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://generativelanguage.googleapis.com"

# Overloaded and rate-limited are worth waiting out; anything else is a real
# answer and retrying it only spends money.
RETRY_CODES = {429, 500, 503}


class GeminiError(RuntimeError):
    def __init__(self, msg, code=None):
        super().__init__(msg)
        self.code = code


class Client:
    def __init__(self, api_key, base=BASE, opener=None, sleep=time.sleep):
        self.key = api_key
        self.base = base
        self._open = opener or urllib.request.urlopen
        self._sleep = sleep

    def _call(self, method, url, body=None, headers=None, timeout=300):
        # Key in a header rather than ?key= so it never shows up in an error
        # message or a proxy log.
        h = {"x-goog-api-key": self.key, **(headers or {})}
        if isinstance(body, dict):
            body = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=h, method=method)
        try:
            with self._open(req, timeout=timeout) as r:
                raw = r.read()
                return r.headers, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            try:
                detail = json.loads(detail)["error"]["message"]
            except (ValueError, KeyError, TypeError):
                detail = detail[:300]
            raise GeminiError(f"Gemini {e.code} on {method} {url}: {detail}",
                              code=e.code) from None

    def upload(self, path, mime="video/mp4"):
        """Resumable upload in two calls: open a session, then send every byte."""
        path = Path(path)
        size = path.stat().st_size
        headers, _ = self._call(
            "POST", f"{self.base}/upload/v1beta/files",
            body={"file": {"display_name": path.name}},
            headers={"X-Goog-Upload-Protocol": "resumable",
                     "X-Goog-Upload-Command": "start",
                     "X-Goog-Upload-Header-Content-Length": str(size),
                     "X-Goog-Upload-Header-Content-Type": mime})
        url = headers.get("X-Goog-Upload-URL")
        if not url:
            raise GeminiError("Gemini did not return an upload URL")
        with open(path, "rb") as f:
            _, info = self._call(
                "POST", url, body=f, timeout=900,
                headers={"Content-Length": str(size),
                         "X-Goog-Upload-Offset": "0",
                         "X-Goog-Upload-Command": "upload, finalize"})
        return info["file"]

    def wait_active(self, f, timeout=600, poll=3):
        """Gemini has to process a video before it can be asked about it."""
        waited = 0
        while f.get("state") == "PROCESSING":
            if waited >= timeout:
                raise GeminiError(f"{f['name']} still processing after {timeout}s")
            self._sleep(poll)
            waited += poll
            _, f = self._call("GET", f"{self.base}/v1beta/{f['name']}")
        if f.get("state") != "ACTIVE":
            raise GeminiError(f"{f['name']} ended {f.get('state')}: {f.get('error')}")
        return f

    def generate(self, model, parts, retries=3):
        """(answer text, usage metadata). Waits out overload and rate limits."""
        model = model.removeprefix("models/")
        url = f"{self.base}/v1beta/models/{model}:generateContent"
        body = {"contents": [{"role": "user", "parts": parts}]}
        for attempt in range(retries + 1):
            try:
                _, r = self._call("POST", url, body=body, timeout=900)
                break
            except GeminiError as e:
                if e.code not in RETRY_CODES or attempt == retries:
                    raise
                wait = 5 * 3 ** attempt
                print(f"  Gemini busy ({e.code}), retrying in {wait}s")
                self._sleep(wait)

        cands = r.get("candidates") or []
        if not cands:
            raise GeminiError(f"Gemini returned no answer: {r.get('promptFeedback')}")
        parts_out = cands[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts_out if not p.get("thought"))
        if not text.strip():
            raise GeminiError(f"Gemini returned an empty answer "
                              f"(finishReason {cands[0].get('finishReason')})")
        return text, r.get("usageMetadata", {})

    def delete(self, name):
        self._call("DELETE", f"{self.base}/v1beta/{name}")

    def models(self):
        """Model ids that can answer a prompt, for when the configured one 404s."""
        _, r = self._call("GET", f"{self.base}/v1beta/models?pageSize=200")
        return [m["name"].removeprefix("models/") for m in r.get("models", [])
                if "generateContent" in m.get("supportedGenerationMethods", [])]
