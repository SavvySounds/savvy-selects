"""Configuration loading. Values come from config.json, falling back to DEFAULTS."""
import json
import os
from pathlib import Path

DEFAULTS = {
    "sources": [],
    "work_dir": str(Path.home() / "SavvySelects"),
    "video_ext": [".mp4", ".mov", ".m4v", ".avi", ".mkv", ".mts", ".lrv"],
    "insta360_ext": [".insv", ".insp"],
    "proxy_height": 720,
    "proxy_crf": 30,
    "hwaccel": True,
    "scene_threshold": 0.30,
    "min_clip_seconds": 2.0,
    "max_clip_seconds": 12.0,
    "sharpness_floor": 45.0,
    "brightness_floor": 28.0,
    "brightness_ceiling": 242.0,
    "model": "claude-sonnet-5",
    "export_vertical": True,
}

REPO_ROOT = Path(__file__).resolve().parent.parent


def config_path() -> Path:
    return Path(os.environ.get("SAVVY_CONFIG", REPO_ROOT / "config.json"))


def load() -> dict:
    cfg = dict(DEFAULTS)
    p = config_path()
    if p.exists():
        cfg.update(json.loads(p.read_text()))
    cfg["work_dir"] = str(Path(cfg["work_dir"]).expanduser())
    return cfg


def work_dir(cfg) -> Path:
    d = Path(cfg["work_dir"])
    d.mkdir(parents=True, exist_ok=True)
    return d
