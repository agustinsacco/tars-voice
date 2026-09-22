PYTHON ?= .venv/bin/python
PIP ?= .venv/bin/pip

.PHONY: setup test check audit build validate

setup:
	python3 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements-dev.txt

test:
	PYTHONPATH=. $(PYTHON) -m unittest discover -s tests -v

check:
	$(PYTHON) -m ruff check gateway tests
	PYTHONPATH=. $(PYTHON) -m compileall -q gateway tests
	node --check static/app.js
	node --check static/audio-worklet.js
	node --check static/sw.js
	$(PYTHON) -m json.tool static/manifest.webmanifest >/dev/null
	bash -n scripts/validate.sh
	@if command -v systemd-analyze >/dev/null 2>&1; then systemd-analyze verify --user deploy/systemd/*.service; fi

audit:
	$(PYTHON) -m pip_audit -r requirements.txt

build: check
	$(PYTHON) -c "from gateway.app import app; assert app.title == 'Tars Voice'"

validate: test build audit
