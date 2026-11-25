#!/usr/bin/env bash
set -euo pipefail

# Clone a separate single-persona installation (1 server/container/DB = 1 persona)
TARGET_DIR="${1:-}"
NEW_CHAR_ID="${2:-}"
NEW_CHAR_NAME="${3:-Cloned Persona}"

if [ -z "$TARGET_DIR" ] || [ -z "$NEW_CHAR_ID" ]; then
  echo "Usage: $0 <target_dir> <new_character_id> [new_character_name]"
  exit 1
fi

mkdir -p "$TARGET_DIR"
if command -v rsync >/dev/null 2>&1; then
  rsync -a \
    --exclude='.venv' \
    --exclude='data' \
    --exclude='web/node_modules' \
    --exclude='web/.next' \
    --exclude='.git' \
    --exclude='__pycache__' \
    ./ "$TARGET_DIR/"
else
  tar --exclude='.venv' \
      --exclude='data' \
      --exclude='web/node_modules' \
      --exclude='web/.next' \
      --exclude='.git' \
      --exclude='__pycache__' \
      -cf - . | (cd "$TARGET_DIR" && tar -xf -)
fi

NEW_MASTER_KEY=""
if command -v python3 >/dev/null 2>&1; then
  NEW_MASTER_KEY=$(python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())' 2>/dev/null || true)
fi

cat > "$TARGET_DIR/.env" <<EOF
HOVITA_ENV=production
CHARACTER_ID=$NEW_CHAR_ID
CHARACTER_NAME=$NEW_CHAR_NAME
DATABASE_URL=sqlite:///./data/${NEW_CHAR_ID}.db
STORAGE_BACKEND=local
LOCAL_STORAGE_DIR=./data/storage_${NEW_CHAR_ID}
SECRET_MASTER_KEY=$NEW_MASTER_KEY
SECRET_STORE_PATH=./data/secrets_${NEW_CHAR_ID}.enc.json
DAILY_BUDGET_USD=3.0
AUTONOMY_LEVEL=L1
PUBLISH_MODE=approval_required
SCHEDULER_INTERVAL_SEC=300
EOF

echo "Cloned isolated single-persona installation for '$NEW_CHAR_ID' at $TARGET_DIR"
