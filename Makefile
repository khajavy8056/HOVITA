.PHONY: install dev test smoke up down lint typecheck migrate seed health clone-install

PYTHON := $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)
PYTEST := $(shell [ -x .venv/bin/pytest ] && echo .venv/bin/pytest || echo pytest)
RUFF := $(shell [ -x .venv/bin/ruff ] && echo .venv/bin/ruff || echo ruff)
MYPY := $(shell [ -x .venv/bin/mypy ] && echo .venv/bin/mypy || echo mypy)
ALEMBIC := $(shell [ -x .venv/bin/alembic ] && echo .venv/bin/alembic || echo alembic)
UVICORN := $(shell [ -x .venv/bin/uvicorn ] && echo .venv/bin/uvicorn || echo uvicorn)

install:
	bash setup.sh --install-only

migrate:
	$(ALEMBIC) upgrade head

seed:
	$(PYTHON) -m hovita.db.seed

health:
	$(PYTHON) scripts/healthcheck.py

lint:
	$(RUFF) check backend/src tests scripts

typecheck:
	$(MYPY) backend/src

test:
	$(PYTEST) -q tests/unit tests/contract tests/integration tests/resilience tests/security

smoke:
	$(PYTHON) scripts/smoke_test.py

dev:
	bash setup.sh --no-smoke
	$(UVICORN) hovita.api.main:app --host 0.0.0.0 --port $${APP_PORT:-8000} --reload

up:
	docker compose up -d --build
	@$(MAKE) health || true

down:
	docker compose down

clone-install:
	bash scripts/clone_install.sh "$(TARGET)" "$(CHAR_ID)" "$(CHAR_NAME)"
