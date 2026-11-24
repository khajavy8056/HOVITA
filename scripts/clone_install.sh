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
rsync -a --exclude='.venv' --exclude='data' --exclude='web/node_modules' --exclude='web/.next' --exclude='.git' ./ "$TARGET_DIR/"

cat > "$TARGET_DIR/.env" <<EOF
HOVITA_ENV=production
CHARACTER_ID=$NEW_CHAR_ID
CHARACTER_NAME=$NEW_CHAR_NAME
DATABASE_URL=sqlite:///./data/${NEW_CHAR_ID}.db
STORAGE_BACKEND=local
LOCAL_STORAGE_DIR=./data/storage_${NEW_CHAR_ID}
SECRET_STORE_PATH=./data/secrets_${NEW_CHAR_ID}.enc.json
DAILY_BUDGET_USD=3.0
AUTONOMY_LEVEL=L1
PUBLISH_MODE=approval_required
EOF

echo "Cloned isolated single-persona installation for '$NEW_CHAR_ID' at $TARGET_DIR"
