from __future__ import annotations

import contextvars
import json
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from hovita.config.settings import get_settings

trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")

_SECRET_PATTERN = re.compile(
    r"(sk-[A-Za-z0-9_\-]{12,}|gsk_[A-Za-z0-9_\-]{12,}|AIza[0-9A-Za-z\-_]{20,}|"
    r"hf_[A-Za-z0-9]{16,}|Bearer\s+[A-Za-z0-9\._\-]{12,}|"
    r"(?:api_key|secret|token|password)=[^\s&\"']+)",
    re.IGNORECASE,
)


def new_trace_id() -> str:
    tid = f"tr-{uuid.uuid4().hex[:16]}"
    trace_id_var.set(tid)
    return tid


def get_trace_id() -> str:
    tid = trace_id_var.get()
    if not tid:
        tid = new_trace_id()
    return tid


def redact_secrets(text: str, extra_secrets: list[str] | None = None) -> str:
    if not text:
        return text
    masked = _SECRET_PATTERN.sub("[REDACTED_SECRET]", text)
    settings = get_settings()
    for sec in settings.collected_secret_values() + (extra_secrets or []):
        if len(sec) >= 4 and sec in masked:
            masked = masked.replace(sec, "[REDACTED_SECRET]")
    return masked


class JsonFormatter(logging.Formatter):
    """Structured JSON formatter with trace_id and automatic secret redaction."""

    def format(self, record: logging.LogRecord) -> str:
        settings = get_settings()
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "character_id": settings.character_id,
            "trace_id": getattr(record, "trace_id", None) or get_trace_id(),
            "message": redact_secrets(record.getMessage()),
        }
        extra_data = getattr(record, "extra_data", None)
        if isinstance(extra_data, dict):
            payload["data"] = json.loads(redact_secrets(json.dumps(extra_data, default=str)))
        if record.exc_info:
            payload["exception"] = redact_secrets(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        settings = get_settings()
        logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
        logger.propagate = False
    return logger
