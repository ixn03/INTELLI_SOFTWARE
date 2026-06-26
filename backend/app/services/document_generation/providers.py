"""Document drafting providers behind a single interface.

No vendor SDKs and no API keys live in code. Real providers (OpenAI,
Anthropic, local, enterprise) are added later behind :class:`LLMDocumentProvider`
and env selection — never called directly from route handlers.

Every provider receives only a :class:`ProviderGenerationInput` (structured
facts + template sections). It must not invent facts; unknowns become
``"Needs engineer input."``
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from app.services.document_generation.models import (
    NEEDS_ENGINEER_INPUT,
    ProviderGenerationInput,
    ProviderGenerationOutput,
    ProviderSectionDraft,
)
from app.services.llm_providers import LLMConfig, load_llm_config_from_env


@runtime_checkable
class LLMDocumentProvider(Protocol):
    """Drafts document prose from structured facts only."""

    name: str

    def generate(self, payload: ProviderGenerationInput) -> ProviderGenerationOutput:
        ...


def _format_facts(facts) -> str:
    parts: list[str] = []
    for fact in facts:
        if fact.present:
            parts.append(f"{fact.label}: {', '.join(fact.values)}")
    return "; ".join(parts)


def _draft_section_from_facts(
    payload: ProviderGenerationInput,
    section: str,
    *,
    prefix: str = "",
) -> ProviderSectionDraft:
    """Deterministic prose for one section, derived strictly from facts."""

    section_facts = payload.facts_for_section(section)
    used_keys = [f.key for f in section_facts]
    if not section_facts:
        return ProviderSectionDraft(
            title=section,
            body_markdown=NEEDS_ENGINEER_INPUT,
            fact_keys=[],
            needs_engineer_input=True,
        )
    detail = _format_facts(section_facts)
    doc_label = payload.record_type.replace("_", " ")
    body = (
        f"{prefix}For {payload.module_name}, the {section.lower()} of this "
        f"{doc_label} is based on the control logic snapshot: {detail}."
    )
    return ProviderSectionDraft(
        title=section,
        body_markdown=body,
        fact_keys=used_keys,
        needs_engineer_input=False,
    )


class DisabledDocumentProvider:
    """LLM off / unavailable: deterministic prose from facts, no invention."""

    name = "disabled"

    def generate(self, payload: ProviderGenerationInput) -> ProviderGenerationOutput:
        sections = [_draft_section_from_facts(payload, s) for s in payload.sections]
        warnings = ["llm_assist_disabled"]
        return ProviderGenerationOutput(
            sections=sections,
            assumptions=[],
            warnings=warnings,
            confidence="low",
            provider_name=self.name,
        )


class FakeDocumentProvider:
    """Test / dev provider with predictable output.

    Records the last :class:`ProviderGenerationInput` it received so tests can
    assert it was handed only structured facts/templates (never raw
    ``parsed_extract`` or uncontrolled prompt soup).
    """

    name = "fake"
    last_payload: Optional[ProviderGenerationInput] = None

    def generate(self, payload: ProviderGenerationInput) -> ProviderGenerationOutput:
        FakeDocumentProvider.last_payload = payload
        sections = [
            _draft_section_from_facts(payload, s, prefix="[AI draft] ")
            for s in payload.sections
        ]
        assumptions: list[str] = []
        if payload.user_notes:
            assumptions.append(
                "Incorporated engineer-provided notes as drafting guidance only."
            )
        if payload.approved_revision_excerpt:
            assumptions.append(
                "Used the existing approved revision for tone/structure context only."
            )
        warnings = ["draft_generated_by_fake_provider"]
        return ProviderGenerationOutput(
            sections=sections,
            assumptions=assumptions,
            warnings=warnings,
            confidence="medium",
            provider_name=self.name,
        )


def resolve_document_provider(
    config: Optional[LLMConfig] = None,
) -> LLMDocumentProvider:
    """Pick a provider implementation from env config. No live HTTP here."""

    cfg = config or load_llm_config_from_env()
    if not cfg.enabled:
        return DisabledDocumentProvider()
    name = (cfg.provider_name or "").lower()
    if name in ("mock", "test", "stub", "fake"):
        return FakeDocumentProvider()
    if name in ("openai", "anthropic"):
        # Future: real providers, only when an SDK + API key wiring exists.
        # Until then, fail safe to deterministic prose rather than inventing.
        return DisabledDocumentProvider()
    return DisabledDocumentProvider()


__all__ = [
    "LLMDocumentProvider",
    "DisabledDocumentProvider",
    "FakeDocumentProvider",
    "resolve_document_provider",
]
