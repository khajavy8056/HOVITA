# HOVITA Deployment, Cloning & Operations Guide

## 1. Clean Server Setup (< 30 Minutes)

Prerequisites: Docker Engine + Docker Compose v2 (for full container stack) or Python 3.11+ & Node.js 22+ (for direct host run).

1. Configure environment secrets:
   ```bash
   cp .env.example .env
   python3 -c "from cryptography.fernet import Fernet; print('SECRET_MASTER_KEY=' + Fernet.generate_key().decode())"
   ```
   Update `.env` with `CHARACTER_ID`, `CHARACTER_NAME`, `DAILY_BUDGET_USD`, and provider keys.
2. Launch production stack:
   ```bash
   make up
   ```
3. Verify schema, seed, and health:
   ```bash
   docker compose exec api alembic upgrade head
   docker compose exec api python -m hovita.db.seed
   docker compose exec api python scripts/healthcheck.py
   ```

## 2. Cloning for Another Persona

Each persona runs in an isolated container/database/directory stack:

```bash
make clone-install TARGET=/opt/hovita-persona-002 CHAR_ID=persona-002 CHAR_NAME="رادین"
```

The clone script generates a fresh `.env` with an independent `CHARACTER_ID`, isolated ports, isolated PostgreSQL/Redis/MinIO volumes, and a new `SECRET_MASTER_KEY`.

## 3. Thin Temporary GPU Notebooks

For Colab, Kaggle, or Lightning AI GPU sessions:
- Open `notebooks/hovita_colab_runner.ipynb`.
- It runs the exact `hovita` codebase with SQLite/local-storage fallback (`HOVITA_ENV=notebook`) and dispatches GPU media batches via `GPUManager` without duplicating pipeline or agent logic.

## 4. Emergency Kill Switch & Audit Verification

- Trigger immediately via UI button (`توقف اضطراری`) or API:
  ```bash
  curl -X POST http://localhost:8000/api/v1/system/emergency-stop \
    -H "Content-Type: application/json" \
    -d '{"reason":"manual incident halt"}'
  ```
  This cancels queued GPU jobs, moves scheduled items back to `awaiting_approval`, blocks all publish endpoints (`HTTP 423`), disables browser automation, and appends an immutable signed record to `audit_log`.
- Verify hash-chain integrity via `GET /api/v1/logs` (`chain_valid: true`).
