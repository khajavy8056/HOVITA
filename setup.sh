#!/usr/bin/env bash
set -euo pipefail

# HOVITA Idempotent Bootstrap Script
# Safe to run repeatedly on every start: install -> env -> migrate -> seed -> healthcheck -> smoke

INSTALL_ONLY=false
RUN_SMOKE=true

for arg in "$@"; do
  case "$arg" in
    --install-only) INSTALL_ONLY=true; RUN_SMOKE=false ;;
    --no-smoke) RUN_SMOKE=false ;;
  esac
done

echo "[setup] Checking Python environment..."
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -e ".[dev]"

if [ ! -f ".env" ] && [ -f ".env.example" ]; then
  echo "[setup] Initializing .env from .env.example..."
  cp .env.example .env
fi

mkdir -p data/storage

# Optional GPU model manifest check
if command -v nvidia-smi >/dev/null 2>&1; then
  echo "[setup] NVIDIA GPU detected — marking GPU_AVAILABLE=true in runtime check."
else
  echo "[setup] No local NVIDIA GPU detected — CPU always-on + free/eco API fallback ready."
fi

if [ -d "web" ] && [ -f "web/package.json" ] && command -v npm >/dev/null 2>&1; then
  echo "[setup] Ensuring Next.js frontend dependencies..."
  (cd web && npm install --silent)
fi

echo "[setup] Running database migrations (idempotent)..."
.venv/bin/alembic upgrade head

echo "[setup] Seeding single-persona installation (idempotent)..."
.venv/bin/python -m hovita.db.seed

echo "[setup] Running healthcheck..."
.venv/bin/python scripts/healthcheck.py

if [ "$INSTALL_ONLY" = "false" ] && [ "$RUN_SMOKE" = "true" ] && [ -f "scripts/smoke_test.py" ]; then
  echo "[setup] Running smoke verification..."
  .venv/bin/python scripts/smoke_test.py
fi

echo "[setup] HOVITA bootstrap completed cleanly."
