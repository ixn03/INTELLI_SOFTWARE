"""AI-assisted document generation layer for INTELLI.

INTELLI is a Control Document Integrity Platform. Parser facts and logic
snapshots are the source of truth. This package lets an LLM *draft language*
from structured facts, but it must never invent engineering facts (setpoints,
IO, alarms, interlocks, permissives, equipment).

Boundary:
- :class:`DocumentGenerationRequest` / :class:`DocumentGenerationResult`
- :class:`GenerationSourceFact` / :class:`GenerationCitation`
- :class:`LLMDocumentProvider` (interface) + :class:`FakeDocumentProvider`
  and future OpenAI/Anthropic providers behind the same interface.

Routes must call :func:`generate_document` (or the wrappers in
``process_knowledge_service``) and never call a vendor SDK directly.
"""

from __future__ import annotations

from app.services.document_generation.models import (
    DocumentGenerationRequest,
    DocumentGenerationResult,
    GenerationCitation,
    GenerationMode,
    GenerationSourceFact,
    GeneratedSection,
    ProviderGenerationInput,
    ProviderGenerationOutput,
    ProviderSectionDraft,
    TemplateSpec,
)
from app.services.document_generation.providers import (
    DisabledDocumentProvider,
    FakeDocumentProvider,
    LLMDocumentProvider,
    OpenAIResponsesDocumentProvider,
    resolve_document_provider,
)
from app.services.document_generation.service import (
    INTELLI_DOC_SYSTEM_PROMPT,
    generate_document,
)

__all__ = [
    "DocumentGenerationRequest",
    "DocumentGenerationResult",
    "GenerationCitation",
    "GenerationMode",
    "GenerationSourceFact",
    "GeneratedSection",
    "ProviderGenerationInput",
    "ProviderGenerationOutput",
    "ProviderSectionDraft",
    "TemplateSpec",
    "DisabledDocumentProvider",
    "FakeDocumentProvider",
    "LLMDocumentProvider",
    "OpenAIResponsesDocumentProvider",
    "resolve_document_provider",
    "INTELLI_DOC_SYSTEM_PROMPT",
    "generate_document",
]
