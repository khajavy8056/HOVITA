from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel, Field

ALLOWED_EXTERNAL_DATA_TOOLS: frozenset[str] = frozenset(
    {
        "get_trends",
        "get_metrics",
        "get_competitors",
        "research_web",
        "extract_page",
        "read_memory",
        "write_memory",
        "lookup_product",
    }
)

_INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "ignore_instructions",
        re.compile(
            r"(ignore\s+(all\s+)?(previous|prior|system)\s+instructions|"
            r"نادیده\s+بگیر\s+دستور|دستورات\s+قبلی\s+را\s+فراموش)",
            re.IGNORECASE,
        ),
    ),
    (
        "system_prompt_exfil",
        re.compile(
            r"(reveal\s+(your\s+)?(system\s+prompt|api\s*key|secrets|env)|"
            r"dump\s+environment|print\s+process\.env|نمایش\s+کلید)",
            re.IGNORECASE,
        ),
    ),
    (
        "privilege_escalation",
        re.compile(
            r"(switch\s+to\s+autonomous|bypass\s+quality\s+gate|publish\s+immediately\s+without|"
            r"execute\s+shell|run\s+bash|rm\s+-rf|sudo\s+)",
            re.IGNORECASE,
        ),
    ),
    (
        "role_override",
        re.compile(
            r"(<\s*/?\s*system\s*>|\[SYSTEM\]|you\s+are\s+now\s+developer\s+mode|"
            r"jailbreak|DAN\s+mode)",
            re.IGNORECASE,
        ),
    ),
]


class PromptInjectionScanResult(BaseModel):
    safe: bool
    risk_score: float = Field(ge=0.0, le=1.0)
    matched_rules: list[str] = Field(default_factory=list)
    sanitized_text: str


@dataclass
class ToolCallPolicyCheck:
    allowed: bool
    reason: str


def scan_external_content(text: str, *, source: str = "external_web") -> PromptInjectionScanResult:
    """Gate anti-injection on external pages, videos, comments, and user-submitted links.

    External content NEVER reaches the OS or system prompt raw; matched injection lines
    are stripped and wrapped in an explicit untrusted-data boundary.
    """
    matched: list[str] = []
    cleaned = text
    for rule_name, pattern in _INJECTION_PATTERNS:
        if pattern.search(cleaned):
            matched.append(rule_name)
            cleaned = pattern.sub("[BLOCKED_INJECTION_FRAGMENT]", cleaned)

    risk_score = min(1.0, len(matched) * 0.45)
    safe = len(matched) == 0
    bounded = (
        f"[UNTRUSTED_EXTERNAL_DATA source={source}]\n"
        f"{cleaned.strip()}\n"
        f"[/UNTRUSTED_EXTERNAL_DATA]"
    )
    return PromptInjectionScanResult(
        safe=safe,
        risk_score=risk_score,
        matched_rules=matched,
        sanitized_text=bounded,
    )


def validate_tool_invocation(
    tool_name: str, *, origin: str = "internal_agent"
) -> ToolCallPolicyCheck:
    """Restrict tool calls triggered from external context to the strict allowlist."""
    if origin == "external_untrusted" and tool_name not in ALLOWED_EXTERNAL_DATA_TOOLS:
        return ToolCallPolicyCheck(
            allowed=False,
            reason=f"Tool '{tool_name}' blocked for untrusted external origin.",
        )
    if tool_name in {"os_exec", "shell", "raw_sql", "browser_login"}:
        return ToolCallPolicyCheck(
            allowed=False,
            reason=f"Dangerous tool '{tool_name}' is structurally prohibited.",
        )
    return ToolCallPolicyCheck(allowed=True, reason="allowed")
