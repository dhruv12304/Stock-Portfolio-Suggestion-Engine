.PHONY: help dev dev-api dev-web install test test-api test-web build lint clean

VENV ?= .venv
PY ?= $(VENV)/bin/python
PIP ?= $(VENV)/bin/pip

help:
	@printf "Targets:\n"
	@printf "  install    Create .venv + install api/ deps, npm install in web/\n"
	@printf "  dev        Run api (8000) and web (5173) together (Ctrl-C to stop)\n"
	@printf "  dev-api    Run only the FastAPI backend\n"
	@printf "  dev-web    Run only the Vite frontend\n"
	@printf "  test       Run pytest + vitest\n"
	@printf "  build      Build the frontend bundle\n"
	@printf "  lint       Lint the Python and TS sources\n"
	@printf "  clean      Remove .venv, web/node_modules, and build artefacts\n"

$(VENV):
	python3 -m venv $(VENV)

install: $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r api/requirements.txt
	cd web && npm install

dev:
	@trap 'kill 0' INT; \
	  $(MAKE) -j2 dev-api dev-web

dev-api:
	$(PY) -m uvicorn api.service.main:app --reload --port 8000

dev-web:
	cd web && npm run dev

test: test-api test-web

test-api:
	$(PY) -m pytest api/tests

test-web:
	cd web && npm test

build:
	cd web && npm run build

lint:
	@which ruff > /dev/null && ruff check api || echo "(ruff not installed; skipping Python lint)"
	cd web && npm run lint || true

clean:
	rm -rf $(VENV) web/node_modules web/dist
