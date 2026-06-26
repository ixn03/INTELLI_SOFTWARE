"""Model / service boundary types for AI-assisted document generation.

These are plain dataclasses (not SQLAlchemy or Pydantic) so the generation
core stays independent of persistence and the web layer. The orchestrator in
``service.py`` converts a :class:`DocumentGenerationResult` into the
``structured_content`` JSON that gets stored on a ``DocumentRevision``.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Optional

NEEDS_ENGINEER_INPUT = "Needs engineer input"


class GenerationMode(str, enum.Enum):
    """How a draft body is produced."""

    DETERMINISTIC_TEMPLATE = "deterministic_template"
    LLM_ASSISTED = "llm_assisted"


@dataclass(frozen=True)
class GenerationSourceFact:
    """A single structured fact drawn from a trusted source.

    ``source_field`` is the provenance reference (e.g.
    ``"LogicSnapshot.parsed_extract.commands"``) so every factual statement in
    the generated draft can be traced back to where it came from.
    """

    key: str
    label: str
    values: list[str]
    source_field: str
    source_kind: str = "parsed_extract"  # parsed_extract | template | approved_revision | user_notes | module
    source_snapshot_id: Optional[str] = None

    @property
    def present(self) -> bool:
        return bool(self.values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "values": list(self.values),
            "source_field": self.source_field,
            "source_kind": self.source_kind,
            "source_snapshot_id": self.source_snapshot_id,
            "present": self.present,
        }


@dataclass(frozen=True)
class GenerationCitation:
    """Provenance reference attaching a drafted section to its source facts."""

    section: str
    fact_keys: list[str]
    source_fields: list[str]
    source_snapshot_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "section": self.section,
            "fact_keys": list(self.fact_keys),
            "source_fields": list(self.source_fields),
            "source_snapshot_id": self.source_snapshot_id,
        }


@dataclass(frozen=True)
class TemplateSpec:
    """The selected template, resolved to an ordered list of sections."""

    id: Optional[str]
    name: str
    source: str  # "company" | "intelli_default"
    sections: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "source": self.source,
            "sections": list(self.sections),
        }


@dataclass
class DocumentGenerationRequest:
    """Everything the generation core needs, assembled from trusted sources."""

    record_type: str
    document_title: str
    mode: GenerationMode
    module_name: str
    source_snapshot_id: str
    source_filename: str
    template: TemplateSpec
    facts: list[GenerationSourceFact]
    selected_sections: Optional[list[str]] = None
    user_notes: Optional[str] = None
    approved_revision_excerpt: Optional[str] = None
    approved_revision_label: Optional[str] = None

    def effective_sections(self) -> list[str]:
        if self.selected_sections:
            wanted = {s.strip().lower() for s in self.selected_sections if s.strip()}
            chosen = [s for s in self.template.sections if s.strip().lower() in wanted]
            if chosen:
                return chosen
        return list(self.template.sections)


@dataclass
class ProviderSectionDraft:
    """One section produced by a provider."""

    title: str
    body_markdown: str
    fact_keys: list[str] = field(default_factory=list)
    needs_engineer_input: bool = False
    assumptions: list[str] = field(default_factory=list)


@dataclass
class ProviderGenerationInput:
    """Sanitized payload handed to an LLM provider.

    This is the ONLY thing a provider sees. It intentionally contains
    structured facts and template sections — never raw ladder/ST/XML, never the
    full ``parsed_extract`` blob, and never an uncontrolled free-text prompt.
    """

    system_prompt: str
    record_type: str
    document_title: str
    module_name: str
    template: TemplateSpec
    sections: list[str]
    facts: list[GenerationSourceFact]
    user_notes: Optional[str] = None
    approved_revision_excerpt: Optional[str] = None

    def facts_for_section(self, section: str) -> list[GenerationSourceFact]:
        from app.services.document_generation.facts import facts_for_section

        return facts_for_section(section, self.record_type, self.facts)


@dataclass
class ProviderGenerationOutput:
    """Structured result returned by a provider (no persistence concerns)."""

    sections: list[ProviderSectionDraft]
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    confidence: str = "medium"
    provider_name: str = "unknown"


@dataclass
class GeneratedSection:
    title: str
    content: str
    facts: list[str]
    needs_engineer_input: bool
    citations: list[GenerationCitation] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "content": self.content,
            "facts": list(self.facts),
            "needs_engineer_input": self.needs_engineer_input,
            "citations": [c.to_dict() for c in self.citations],
            "assumptions": list(self.assumptions),
        }


@dataclass
class DocumentGenerationResult:
    """Final generation output. Maps directly to the required output contract."""

    body_markdown: str
    structured_content: dict[str, Any]
    source_snapshot_id: str
    template_id: Optional[str]
    facts_used: list[GenerationSourceFact]
    missing_facts: list[GenerationSourceFact]
    assumptions: list[str]
    confidence: str
    warnings: list[str]
    mode: GenerationMode
    provider_name: str = "deterministic"

    def summary_dict(self) -> dict[str, Any]:
        return {
            "generation_mode": self.mode.value,
            "provider_name": self.provider_name,
            "source_snapshot_id": self.source_snapshot_id,
            "template_id": self.template_id,
            "confidence": self.confidence,
            "facts_used": [f.to_dict() for f in self.facts_used],
            "missing_facts": [f.to_dict() for f in self.missing_facts],
            "assumptions": list(self.assumptions),
            "warnings": list(self.warnings),
        }


__all__ = [
    "NEEDS_ENGINEER_INPUT",
    "GenerationMode",
    "GenerationSourceFact",
    "GenerationCitation",
    "TemplateSpec",
    "DocumentGenerationRequest",
    "ProviderSectionDraft",
    "ProviderGenerationInput",
    "ProviderGenerationOutput",
    "GeneratedSection",
    "DocumentGenerationResult",
]
