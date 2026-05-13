"""Compatibility re-exports — use ``llm_assist_service`` and ``llm_providers``."""

from __future__ import annotations

from app.services.llm_assist_service import answer_with_llm_assist
from app.services.llm_providers import MockLLMProvider as MockLlmProvider

__all__ = ["MockLlmProvider", "answer_with_llm_assist"]
