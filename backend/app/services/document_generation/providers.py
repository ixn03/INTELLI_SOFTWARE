"""Document drafting providers behind a single interface.

No vendor SDKs and no API keys live in code. Real providers (OpenAI,
Anthropic, local, enterprise) are added later behind :class:`LLMDocumentProvider`
and env selection — never called directly from route handlers.

Every provider receives only a :class:`ProviderGenerationInput` (structured
facts + template sections). It must not invent facts; unknowns become
``"Needs engineer input."``
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional, Protocol, runtime_checkable
from urllib import request as urllib_request

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


def _fact_payload(payload: ProviderGenerationInput) -> list[dict[str, Any]]:
    """Serialize only trusted facts for a remote drafting provider."""

    return [
        {
            "key": fact.key,
            "label": fact.label,
            "values": list(fact.values),
            "source_field": fact.source_field,
            "source_snapshot_id": fact.source_snapshot_id,
            "present": fact.present,
        }
        for fact in payload.facts
    ]


def _extract_response_text(response_json: dict[str, Any]) -> str:
    """Read text from OpenAI Responses API shapes without an SDK dependency."""

    direct = response_json.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    chunks: list[str] = []
    for output in response_json.get("output") or []:
        if not isinstance(output, dict):
            continue
        for content in output.get("content") or []:
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "\n".join(chunks).strip()


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, flags=re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()
    return json.loads(cleaned)


class OpenAIResponsesDocumentProvider:
    """OpenAI Responses API provider for structured-fact document drafts."""

    name = "openai"

    def __init__(self, config: LLMConfig, api_key: str, base_url: str | None = None) -> None:
        self.config = config
        self.api_key = api_key
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")

    def generate(self, payload: ProviderGenerationInput) -> ProviderGenerationOutput:
        request_payload = {
            "document_title": payload.document_title,
            "record_type": payload.record_type,
            "module_name": payload.module_name,
            "template": payload.template.to_dict(),
            "sections": list(payload.sections),
            "facts": _fact_payload(payload),
            "user_notes": payload.user_notes,
            "approved_revision_excerpt": payload.approved_revision_excerpt,
            "output_contract": {
                "sections": [
                    {
                        "title": "section title from requested sections",
                        "body_markdown": "prose using only supplied fact values",
                        "fact_keys": ["fact keys used"],
                        "needs_engineer_input": False,
                        "assumptions": ["optional assumptions"],
                    }
                ],
                "assumptions": ["global assumptions"],
                "warnings": ["optional warnings"],
                "confidence": "low|medium|high",
            },
        }
        instructions = (
            f"{payload.system_prompt}\n\n"
            "Return only valid JSON matching output_contract. Use only supplied fact values "
            "for engineering claims. For missing information, write 'Needs engineer input' "
            "and set needs_engineer_input true. Do not infer setpoints, interlocks, tags, "
            "or cause/effect relationships that are not in the facts."
        )
        body = json.dumps(
            {
                "model": self.config.model_name or "gpt-5.5",
                "instructions": instructions,
                "input": json.dumps(request_payload, indent=2),
                "max_output_tokens": self.config.max_tokens,
            }
        ).encode("utf-8")
        req = urllib_request.Request(
            f"{self.base_url}/responses",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib_request.urlopen(req, timeout=60) as response:
            response_body = response.read().decode("utf-8")
        text = _extract_response_text(json.loads(response_body))
        try:
            data = _parse_json_object(text)
        except (json.JSONDecodeError, TypeError):
            return ProviderGenerationOutput(
                sections=[
                    ProviderSectionDraft(
                        title=payload.sections[0] if payload.sections else "Draft",
                        body_markdown=NEEDS_ENGINEER_INPUT,
                        fact_keys=[],
                        needs_engineer_input=True,
                        assumptions=["Provider returned non-JSON output; engineer review required."],
                    )
                ],
                assumptions=[],
                warnings=["openai_invalid_json"],
                confidence="low",
                provider_name=self.name,
            )

        sections: list[ProviderSectionDraft] = []
        for section in data.get("sections") or []:
            if not isinstance(section, dict):
                continue
            title = str(section.get("title") or "").strip()
            if not title:
                continue
            allowed = {s.lower() for s in payload.sections}
            if title.lower() not in allowed:
                continue
            fact_keys = [
                str(key)
                for key in section.get("fact_keys") or []
                if isinstance(key, (str, int, float))
            ]
            sections.append(
                ProviderSectionDraft(
                    title=title,
                    body_markdown=str(section.get("body_markdown") or NEEDS_ENGINEER_INPUT),
                    fact_keys=fact_keys,
                    needs_engineer_input=bool(section.get("needs_engineer_input")),
                    assumptions=[
                        str(item)
                        for item in section.get("assumptions") or []
                        if isinstance(item, (str, int, float))
                    ],
                )
            )
        if not sections:
            sections = [
                ProviderSectionDraft(
                    title=section,
                    body_markdown=NEEDS_ENGINEER_INPUT,
                    fact_keys=[],
                    needs_engineer_input=True,
                )
                for section in payload.sections
            ]
        return ProviderGenerationOutput(
            sections=sections,
            assumptions=[
                str(item)
                for item in data.get("assumptions") or []
                if isinstance(item, (str, int, float))
            ],
            warnings=[
                str(item)
                for item in data.get("warnings") or []
                if isinstance(item, (str, int, float))
            ],
            confidence=str(data.get("confidence") or "medium"),
            provider_name=self.name,
        )


def resolve_document_provider(
    config: Optional[LLMConfig] = None,
) -> LLMDocumentProvider:
    """Pick a provider implementation from env config."""

    cfg = config or load_llm_config_from_env()
    if not cfg.enabled:
        return DisabledDocumentProvider()
    name = (cfg.provider_name or "").lower()
    if name in ("mock", "test", "stub", "fake"):
        return FakeDocumentProvider()
    if name == "openai":
        api_key = os.environ.get("OPENAI_API_KEY")
        if api_key:
            return OpenAIResponsesDocumentProvider(
                cfg,
                api_key,
                os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_BASE_URL"),
            )
        return DisabledDocumentProvider()
    if name == "anthropic":
        return DisabledDocumentProvider()
    return DisabledDocumentProvider()


__all__ = [
    "LLMDocumentProvider",
    "DisabledDocumentProvider",
    "FakeDocumentProvider",
    "OpenAIResponsesDocumentProvider",
    "resolve_document_provider",
]
