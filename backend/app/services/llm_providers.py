"""LLM provider abstraction for INTELLI Assist (v1).

No vendor SDKs and no API keys in code. Real providers (OpenAI, Anthropic,
local, enterprise) can be added later behind the same interface and env
selection — not implemented until explicit env wiring exists.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional, Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    """Rewrites structured evidence only; must not invent process facts."""

    def generate_answer(self, system_prompt: str, evidence_package: dict) -> str:
        ...


@dataclass(frozen=True)
class LLMConfig:
    """Runtime LLM assist configuration (env-driven)."""

    provider_name: str
    enabled: bool
    model_name: str
    temperature: float
    max_tokens: int


def load_llm_config_from_env() -> LLMConfig:
    """Load flags and tuning from environment (defaults: off, mock)."""

    raw_enable = os.environ.get("ENABLE_LLM_ASSIST", "false").lower()
    enabled = raw_enable in ("1", "true", "yes", "on")
    provider = (os.environ.get("LLM_PROVIDER_NAME") or "mock").strip().lower()
    model = (os.environ.get("LLM_MODEL_NAME") or "mock").strip()
    try:
        temperature = float(os.environ.get("LLM_TEMPERATURE", "0.2"))
    except ValueError:
        temperature = 0.2
    try:
        max_tokens = int(os.environ.get("LLM_MAX_TOKENS", "2048"))
    except ValueError:
        max_tokens = 2048
    return LLMConfig(
        provider_name=provider,
        enabled=enabled,
        model_name=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def engineering_paragraph_from_evidence(evidence_package: dict[str, Any]) -> str:
    """Deterministic 'rewrite' of evidence into short engineering prose (no LLM)."""

    style = str(evidence_package.get("answer_style") or "controls_engineer")
    if evidence_package.get("target_resolution") == "unresolved":
        cands = evidence_package.get("suggested_target_candidates") or []
        hint = (
            f" Possible tag matches to try: {', '.join(cands[:8])}."
            if cands
            else ""
        )
        return (
            "Target not resolved from the question against this project."
            + hint
            + " Select a tag in the object list or name the tag explicitly."
        )

    parts: list[str] = []
    if style == "detailed_reasoning":
        trust = evidence_package.get("trust_assessment") or {}
        if isinstance(trust, dict) and trust.get("confidence_score") is not None:
            parts.append(f"Confidence score: {trust.get('confidence_score')}.")
    verdict = evidence_package.get("runtime_verdict")
    if verdict:
        parts.append(
            f"Operational verdict: {str(verdict).replace('_', ' ')}."
        )
    block = evidence_package.get("blocking_conditions") or []
    if block:
        parts.append("Blocking conditions: " + "; ".join(str(x) for x in block[:8]) + ".")
    sat = evidence_package.get("satisfied_conditions") or []
    if sat:
        parts.append("Satisfied: " + "; ".join(str(x) for x in sat[:6]) + ".")
    miss = evidence_package.get("missing_conditions") or []
    if miss:
        parts.append("Missing runtime values: " + "; ".join(str(x) for x in miss[:6]) + ".")
    unsup = evidence_package.get("unsupported_conditions") or []
    if unsup:
        parts.append(
            "Unsupported or too-complex for runtime check: "
            + "; ".join(str(x) for x in unsup[:6])
            + "."
        )
    seq = evidence_package.get("sequence_summary") or []
    if seq:
        parts.append("Sequence / state: " + " ".join(str(s) for s in seq[:5]) + ".")
    sem = evidence_package.get("sequence_semantics") or {}
    if isinstance(sem, dict) and style in {"controls_engineer", "detailed_reasoning"}:
        waits = sem.get("likely_waiting_conditions") or []
        faults = sem.get("fault_conditions") or []
        if waits:
            parts.append("Waiting/completion evidence: " + "; ".join(str(x) for x in waits[:3]) + ".")
        if faults:
            parts.append("Fault/interlock evidence: " + "; ".join(str(x) for x in faults[:3]) + ".")
    kn = evidence_package.get("knowledge_notes") or []
    if kn:
        parts.append("Knowledge notes: " + " ".join(str(k) for k in kn[:4]) + ".")

    ts = evidence_package.get("trace_summary") or ""
    if ts and style != "concise_operator":
        parts.append(str(ts).strip()[:1200])

    if not parts:
        conc = evidence_package.get("deterministic_conclusions") or []
        if conc:
            parts.append(" ".join(str(c) for c in conc[:4]))

    answer = " ".join(parts).strip() or "No structured evidence was produced for this question."
    if style == "concise_operator":
        sentences = [s.strip() for s in answer.split(".") if s.strip()]
        return ". ".join(sentences[:3]) + ("." if sentences else "")
    return answer


class DisabledLLMProvider:
    """Feature off or no provider: answer is purely evidence-derived text."""

    def generate_answer(self, system_prompt: str, evidence_package: dict) -> str:
        _ = system_prompt
        return engineering_paragraph_from_evidence(evidence_package)


class MockLLMProvider:
    """Test / dev provider: uses only the evidence dict; optional test hooks.

    Set ``evidence_package["_mock_answer"]`` in tests to simulate an LLM line.
    Otherwise returns the same deterministic paragraph as :class:`DisabledLLMProvider`
    with an ``[Assist]`` prefix so tests can tell mock was invoked.
    """

    last_evidence_package: Optional[dict] = None

    def generate_answer(self, system_prompt: str, evidence_package: dict) -> str:
        _ = system_prompt
        MockLLMProvider.last_evidence_package = dict(evidence_package)
        override = evidence_package.get("_mock_answer")
        if override is not None:
            return str(override)
        base = engineering_paragraph_from_evidence(evidence_package)
        return f"[Assist] {base}"


def resolve_llm_provider(config: LLMConfig) -> LLMProvider:
    """Pick a provider implementation. No live HTTP calls here."""

    if not config.enabled:
        return DisabledLLMProvider()
    name = config.provider_name
    if name in ("mock", "test", "stub"):
        return MockLLMProvider()
    # Future: openai, anthropic, ollama, etc. — only when env + client exist.
    return DisabledLLMProvider()


__all__ = [
    "LLMConfig",
    "LLMProvider",
    "DisabledLLMProvider",
    "MockLLMProvider",
    "engineering_paragraph_from_evidence",
    "load_llm_config_from_env",
    "resolve_llm_provider",
]
