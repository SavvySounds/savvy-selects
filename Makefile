.PHONY: setup test proxy scan score review export crate status calibrate clean

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

clean:
	rm -rf __pycache__ .pytest_cache **/__pycache__
