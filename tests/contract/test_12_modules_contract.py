from __future__ import annotations

import ast
from pathlib import Path

from hovita.contracts.base import ProviderTier
from hovita.modules.bootstrap import build_default_registry


def test_all_12_modules_registered_with_free_first_s0() -> None:
    reg = build_default_registry()
    expected = {
        "llm",
        "memory",
        "research",
        "image_gen",
        "video_gen",
        "motion_transfer",
        "tts",
        "stt",
        "video_edit",
        "social_analytics",
        "publisher",
        "workflow_engine",
    }
    listed = reg.list_providers()
    assert set(listed.keys()) == expected
    for mod in expected:
        candidates = reg.ordered_candidates(mod, policy="free_first")
        assert len(candidates) >= 1, f"Module {mod} has no registered providers"
        assert candidates[0].metadata.tier == ProviderTier.FREE, (
            f"First provider for {mod} must be FREE/S0, got {candidates[0].metadata.tier}"
        )


def test_paid_upgrade_gate_requires_25pct_improvement() -> None:
    reg = build_default_registry()
    # ollama_local quality=0.72 vs openrouter_eco=0.86 -> (0.86-0.72)/0.72 = 19.4% < 25% -> BLOCKED
    assert not reg.can_upgrade_to_paid(
        "llm", "ollama_local", "openrouter_eco", qa_failed_on_free=False
    )
    # ollama_local quality=0.72 vs claude_pro=0.95 -> (0.95-0.72)/0.72 = 31.9% >= 25% -> ALLOWED
    assert reg.can_upgrade_to_paid(
        "llm", "ollama_local", "claude_pro", qa_failed_on_free=False
    )
    # Even <25% gain is ALLOWED if free QA gate failed
    assert reg.can_upgrade_to_paid(
        "llm", "ollama_local", "openrouter_eco", qa_failed_on_free=True
    )


def test_zero_external_vendor_sdk_imports_in_core_packages() -> None:
    forbidden_modules = {"openai", "anthropic", "tavily", "elevenlabs", "fal_client"}
    core_dirs = [
        Path("backend/src/hovita/agents"),
        Path("backend/src/hovita/contracts"),
        Path("backend/src/hovita/economy"),
        Path("backend/src/hovita/security"),
    ]
    for cdir in core_dirs:
        for py in cdir.rglob("*.py"):
            tree = ast.parse(py.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".")[0]
                        assert top not in forbidden_modules, f"Forbidden SDK '{top}' in {py}"
                elif isinstance(node, ast.ImportFrom) and node.module:
                    top = node.module.split(".")[0]
                    assert top not in forbidden_modules, f"Forbidden SDK '{top}' in {py}"
