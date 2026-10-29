.PHONY: setup test proxy scan score review export crate status calibrate skill clean

setup:
	python3 -m venv .venv
	.venv/bin/pip install -e ".[dev]"
	@test -f config.json || cp config.example.json config.json
	@echo "Edit config.json with your real paths, then: make proxy"

test:
	.venv/bin/pytest -q

proxy:   ; .venv/bin/savvy proxy
scan:    ; .venv/bin/savvy scan
score:   ; .venv/bin/savvy score --limit 200
review:  ; .venv/bin/savvy review
export:  ; .venv/bin/savvy export --use-ratings --min-rating 4
crate:   ; .venv/bin/savvy export --crate
status:  ; .venv/bin/savvy status
calibrate: ; .venv/bin/savvy calibrate

# /watch works in any Claude Code session, not just this repo. A symlink, so
# edits here reach it without reinstalling.
skill:
	mkdir -p ~/.claude/skills
	ln -sfn "$(CURDIR)/.claude/skills/watch" ~/.claude/skills/watch
	@echo "Installed /watch. It needs GEMINI_API_KEY set in your shell."

clean:
	rm -rf __pycache__ .pytest_cache **/__pycache__
