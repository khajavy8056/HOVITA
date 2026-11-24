from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet

from hovita.config.settings import Settings, get_settings


class SecretStore:
    """Encrypted server-side secret store supporting provider isolation and key rotation.

    Secrets are NEVER persisted into DB tables or logs.
    """

    def __init__(self, store_path: Path | None = None, master_key: str | None = None) -> None:
        settings = get_settings()
        self.store_path = store_path or settings.secret_store_path
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        raw_key = master_key or settings.secret_master_key.get_secret_value()
        if not raw_key:
            # Derive deterministic local key from environment salt or generate ephemeral file key
            key_file = self.store_path.with_suffix(".key")
            if key_file.exists():
                self._fernet_key = key_file.read_bytes().strip()
            else:
                self._fernet_key = Fernet.generate_key()
                key_file.write_bytes(self._fernet_key)
                os.chmod(key_file, 0o600)
        else:
            digest = hashlib.sha256(raw_key.encode("utf-8")).digest()
            self._fernet_key = base64.urlsafe_b64encode(digest)
        self._fernet = Fernet(self._fernet_key)
        self._env_fallback_map = self._build_env_map(settings)

    @staticmethod
    def _build_env_map(settings: Settings) -> dict[str, str]:
        mapping: dict[str, str] = {
            "freellmapi": settings.freellmapi_key.get_secret_value(),
            "openrouter": settings.openrouter_api_key.get_secret_value(),
            "groq": settings.groq_api_key.get_secret_value(),
            "anthropic": settings.anthropic_api_key.get_secret_value(),
            "gemini": settings.gemini_api_key.get_secret_value(),
            "fal": settings.fal_key.get_secret_value(),
            "huggingface": settings.hf_token.get_secret_value(),
            "meta_secret": settings.meta_app_secret.get_secret_value(),
            "instagram": settings.instagram_access_token.get_secret_value(),
            "tiktok": settings.tiktok_access_token.get_secret_value(),
            "tiktok_secret": settings.tiktok_client_secret.get_secret_value(),
            "youtube": settings.youtube_api_key.get_secret_value(),
            "youtube_oauth": settings.youtube_oauth_token.get_secret_value(),
            "tavily": settings.tavily_api_key.get_secret_value(),
            "firecrawl": settings.firecrawl_api_key.get_secret_value(),
            "ensembledata": settings.ensembledata_api_key.get_secret_value(),
            "apify": settings.apify_token.get_secret_value(),
            "mem0": settings.mem0_api_key.get_secret_value(),
            "langfuse_secret": settings.langfuse_secret_key.get_secret_value(),
            "livekit_key": settings.livekit_api_key.get_secret_value(),
            "livekit_secret": settings.livekit_api_secret.get_secret_value(),
            "heygen": settings.heygen_api_key.get_secret_value(),
            "tavus": settings.tavus_api_key.get_secret_value(),
            "d_id": settings.d_id_api_key.get_secret_value(),
            "n8n": settings.n8n_api_key.get_secret_value(),
        }
        return {k: v for k, v in mapping.items() if v}

    def _read_store(self) -> dict[str, Any]:
        if not self.store_path.exists():
            return {"providers": {}}
        encrypted = self.store_path.read_bytes()
        if not encrypted:
            return {"providers": {}}
        raw = self._fernet.decrypt(encrypted)
        data: dict[str, Any] = json.loads(raw.decode("utf-8"))
        return data

    def _write_store(self, data: dict[str, Any]) -> None:
        raw = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
        encrypted = self._fernet.encrypt(raw)
        self.store_path.write_bytes(encrypted)
        os.chmod(self.store_path, 0o600)

    def set_provider_keys(self, provider: str, keys: list[str]) -> None:
        clean_keys = [k.strip() for k in keys if k.strip()]
        data = self._read_store()
        data.setdefault("providers", {})[provider] = {
            "keys": clean_keys,
            "active_index": 0,
            "rotated_at": datetime.now(UTC).isoformat(),
        }
        self._write_store(data)

    def rotate_provider_key(self, provider: str, new_key: str) -> int:
        data = self._read_store()
        entry = data.setdefault("providers", {}).setdefault(provider, {"keys": [], "active_index": 0})
        keys = list(entry.get("keys", []))
        if new_key.strip() and new_key.strip() not in keys:
            keys.append(new_key.strip())
        entry["keys"] = keys
        entry["active_index"] = len(keys) - 1 if keys else 0
        entry["rotated_at"] = datetime.now(UTC).isoformat()
        self._write_store(data)
        return len(keys)

    def get_keys(self, provider: str) -> list[str]:
        data = self._read_store()
        stored = data.get("providers", {}).get(provider, {}).get("keys", [])
        if stored:
            return [str(k) for k in stored if str(k).strip()]
        env_val = self._env_fallback_map.get(provider, "")
        if env_val:
            # Support comma-separated multi-key pools in env vars
            return [k.strip() for k in env_val.split(",") if k.strip()]
        return []

    def get_primary_key(self, provider: str) -> str | None:
        keys = self.get_keys(provider)
        return keys[0] if keys else None

    def list_configured_providers(self) -> dict[str, dict[str, Any]]:
        data = self._read_store()
        providers: dict[str, dict[str, Any]] = {}
        all_names = set(data.get("providers", {}).keys()) | set(self._env_fallback_map.keys())
        for name in sorted(all_names):
            keys = self.get_keys(name)
            providers[name] = {
                "configured": len(keys) > 0,
                "key_count": len(keys),
                "source": "encrypted_store" if name in data.get("providers", {}) else "env",
            }
        return providers

    def all_known_secrets(self) -> list[str]:
        secrets = list(self._env_fallback_map.values())
        data = self._read_store()
        for entry in data.get("providers", {}).values():
            for k in entry.get("keys", []):
                if isinstance(k, str) and len(k.strip()) >= 4:
                    secrets.append(k.strip())
        # split comma-separated values too
        expanded: list[str] = []
        for s in secrets:
            for part in s.split(","):
                p = part.strip()
                if len(p) >= 4:
                    expanded.append(p)
        return list(dict.fromkeys(expanded))
