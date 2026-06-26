"""Document generation orchestrator.

Assembles a sanitized provider payload from trusted facts, runs the selected
generation mode, applies an anti-hallucination guard, and returns a
:class:`DocumentGenerationResult` ready to persist on a ``DocumentRevision``.
"""

from __future__ import annotations

import re
from typing import Optional

from app.services.document_generation.facts import allowed_fact_tokens
from app.services.document_generation.models import (
    NEEDS_ENGINEER_INPUT,
    DocumentGenerationRequest,
    DocumentGenerationResult,
    GeneratedSection,
    GenerationCitation,
    GenerationMode,
    GenerationSourceFact,
    ProviderGenerationInput,
    ProviderGenerationOutput,
    ProviderSectionDraft,
)
from app.services.document_generation.providers import (
    LLMDocumentProvider,
    resolve_document_provider,
)
from app.services.llm_providers import LLMConfig


INTELLI_DOC_SYSTEM_PROMPT = """You are INTELLI, a controls-engineering document drafting assistant.

INTELLI is a Control Document Integrity Platform. The parser facts and logic
snapshot provided to you are the source of truth.

Hard rules:
- Write engineering prose ONLY from the structured facts and template provided.
- NEVER invent setpoints, IO, alarms, interlocks, permissives, or equipment.
- NEVER introduce tag names, devices, or values that are not in the supplied facts.
- If a section has no supporting fact, write exactly: "Needs engineer input."
- Every factual statement must be traceable to a supplied source field.
- Treat the existing approved revision (if any) as tone/structure context only;
  do not copy unverified facts from it.
- Treat engineer-provided notes as drafting guidance, not as new facts.
- Prefer concise, professional controls-engineering wording.
"""

_IDENTIFIER_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*\b")

_GUARD_STOPWORDS = {
    "needs",
    "engineer",
    "input",
    "ai",
    "draft",
    "the",
    "for",
    "of",
    "is",
    "are",
    "based",
    "on",
    "control",
    "logic",
    "snapshot",
    "this",
    "and",
    "from",
    "intelli",
    "section",
    "needs_engineer_input",
}


def _build_provider_input(request: DocumentGenerationRequest) -> ProviderGenerationInput:
    """Build the sanitized payload. Only structured facts/templates cross here."""

    return ProviderGenerationInput(
        system_prompt=INTELLI_DOC_SYSTEM_PROMPT,
        record_type=request.record_type,
        document_title=request.document_title,
        module_name=request.module_name,
        template=request.template,
        sections=request.effective_sections(),
        facts=list(request.facts),
        user_notes=request.user_notes,
        approved_revision_excerpt=request.approved_revision_excerpt,
    )


def _format_facts_bullets(values: list[str]) -> str:
    if not values:
        return NEEDS_ENGINEER_INPUT
    return "\n".join(f"- {value}" for value in values)


def _render_deterministic(request: DocumentGenerationRequest) -> ProviderGenerationOutput:
    """Deterministic, facts-only rendering (bullet lists). No LLM involved."""

    from app.services.document_generation.facts import facts_for_section

    sections: list[ProviderSectionDraft] = []
    for section in request.effective_sections():
        section_facts = facts_for_section(section, request.record_type, request.facts)
        values: list[str] = []
        used_keys: list[str] = []
        for fact in section_facts:
            values.extend(fact.values)
            used_keys.append(fact.key)
        needs_input = not values
        sections.append(
            ProviderSectionDraft(
                title=section,
                body_markdown=_format_facts_bullets(values),
                fact_keys=used_keys,
                needs_engineer_input=needs_input,
            )
        )
    return ProviderGenerationOutput(
        sections=sections,
        assumptions=[],
        warnings=[],
        confidence="deterministic",
        provider_name="deterministic_template",
    )


def _hallucination_warnings(
    body: str,
    facts: list[GenerationSourceFact],
    module_name: str,
) -> list[str]:
    """Flag identifier-like tokens not present in the supplied facts."""

    allowed = {t.lower() for t in allowed_fact_tokens(facts)}
    allowed.update(p.lower() for p in module_name.replace("_", " ").split())
    allowed.add(module_name.lower())
    warnings: list[str] = []
    seen: set[str] = set()
    for match in _IDENTIFIER_RE.finditer(body):
        token = match.group(0)
        looks_like_id = "_" in token or "." in token or (token.isupper() and len(token) >= 3)
        if not looks_like_id:
            continue
        low = token.lower()
        if low in _GUARD_STOPWORDS or low in allowed:
            continue
        if any(low in a or a.endswith(low) for a in allowed if "." in a or "_" in a):
            continue
        if token in seen:
            continue
        seen.add(token)
        warnings.append(f"draft_mentions_unlisted_token:{token}")
    return warnings[:12]


def _assemble_result(
    request: DocumentGenerationRequest,
    output: ProviderGenerationOutput,
    *,
    mode: GenerationMode,
    provider_name: str,
) -> DocumentGenerationResult:
    facts_by_key = {f.key: f for f in request.facts}
    drafts_by_title = {d.title: d for d in output.sections}

    lines = [f"# {request.document_title}", ""]
    structured_sections: list[dict] = []
    generated: list[GeneratedSection] = []

    for section in request.effective_sections():
        draft = drafts_by_title.get(section) or ProviderSectionDraft(
            title=section,
            body_markdown=NEEDS_ENGINEER_INPUT,
            needs_engineer_input=True,
        )
        citation_facts = [facts_by_key[k] for k in draft.fact_keys if k in facts_by_key]
        citation = GenerationCitation(
            section=section,
            fact_keys=[f.key for f in citation_facts],
            source_fields=[f.source_field for f in citation_facts],
            source_snapshot_id=request.source_snapshot_id,
        )
        gen = GeneratedSection(
            title=section,
            content=draft.body_markdown,
            facts=[v for f in citation_facts for v in f.values],
            needs_engineer_input=draft.needs_engineer_input,
            citations=[citation] if citation_facts else [],
            assumptions=draft.assumptions,
        )
        generated.append(gen)
        lines.extend([f"## {section}", "", draft.body_markdown, ""])
        structured_sections.append(gen.to_dict())

    body_markdown = "\n".join(lines).strip() + "\n"

    facts_used = [f for f in request.facts if f.present]
    missing_facts = [f for f in request.facts if not f.present]

    warnings = list(output.warnings)
    if mode == GenerationMode.LLM_ASSISTED:
        warnings.extend(
            _hallucination_warnings(body_markdown, request.facts, request.module_name)
        )
    if missing_facts:
        warnings.append(
            f"{len(missing_facts)} fact group(s) missing; marked as '{NEEDS_ENGINEER_INPUT}'."
        )

    structured_content = {
        "generation": mode.value,
        "provider_name": provider_name,
        "template": request.template.to_dict(),
        "source_snapshot_id": request.source_snapshot_id,
        "source_filename": request.source_filename,
        "record_type": request.record_type,
        "sections": structured_sections,
        "facts_used": [f.to_dict() for f in facts_used],
        "missing_facts": [f.to_dict() for f in missing_facts],
        "assumptions": list(output.assumptions),
        "confidence": output.confidence,
        "warnings": warnings,
        "user_notes_provided": bool(request.user_notes),
        "approved_revision_used_as_context": bool(request.approved_revision_excerpt),
    }
    if request.approved_revision_label:
        structured_content["approved_revision_label"] = request.approved_revision_label

    return DocumentGenerationResult(
        body_markdown=body_markdown,
        structured_content=structured_content,
        source_snapshot_id=request.source_snapshot_id,
        template_id=request.template.id,
        facts_used=facts_used,
        missing_facts=missing_facts,
        assumptions=list(output.assumptions),
        confidence=output.confidence,
        warnings=warnings,
        mode=mode,
        provider_name=provider_name,
    )


def generate_document(
    request: DocumentGenerationRequest,
    *,
    provider: Optional[LLMDocumentProvider] = None,
    llm_config: Optional[LLMConfig] = None,
) -> DocumentGenerationResult:
    """Generate a draft in the requested mode.

    - ``deterministic_template``: facts-only rendering, no provider involved.
    - ``llm_assisted``: hand a sanitized payload to a provider abstraction.
    """

    if request.mode == GenerationMode.DETERMINISTIC_TEMPLATE:
        output = _render_deterministic(request)
        return _assemble_result(
            request,
            output,
            mode=GenerationMode.DETERMINISTIC_TEMPLATE,
            provider_name=output.provider_name,
        )

    prov = provider or resolve_document_provider(llm_config)
    payload = _build_provider_input(request)
    output = prov.generate(payload)
    return _assemble_result(
        request,
        output,
        mode=GenerationMode.LLM_ASSISTED,
        provider_name=output.provider_name or getattr(prov, "name", "unknown"),
    )


__all__ = [
    "INTELLI_DOC_SYSTEM_PROMPT",
    "generate_document",
]
