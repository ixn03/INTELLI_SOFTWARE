"""Tests for the AI-assisted document generation layer.

Covers the provider abstraction (structured facts only), the fake provider's
predictable output, the persistence wrapper (DRAFT + ReviewItem, approved
revisions preserved), missing-fact handling, and deterministic mode.
"""

from __future__ import annotations

import os
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.db.models.process_knowledge import (  # noqa: E402
    DocumentRevisionStatus,
    EngineeringRecordType,
    ReviewItemStatus,
    SnapshotSourceType,
)
from app.db.session import get_session_factory, init_db, reset_engine_cache  # noqa: E402
from app.schemas.process_knowledge import (  # noqa: E402
    DocumentRevisionCreate,
    DocumentTemplateCreate,
    EquipmentModuleCreate,
    EngineeringRecordCreate,
    LogicSnapshotCreate,
    ProcessUnitCreate,
)
from app.services import process_knowledge_service as svc  # noqa: E402
from app.services.project_store import project_store  # noqa: E402
from app.services.document_generation import (  # noqa: E402
    DocumentGenerationRequest,
    FakeDocumentProvider,
    GenerationMode,
    GenerationSourceFact,
    OpenAIResponsesDocumentProvider,
    TemplateSpec,
    generate_document,
)
from app.services.document_generation.facts import build_source_facts  # noqa: E402
from app.services.document_generation.models import ProviderGenerationInput  # noqa: E402
from app.services.llm_providers import LLMConfig  # noqa: E402


class _RecordingProvider:
    """Captures exactly what the generation core hands a provider."""

    name = "recording"

    def __init__(self) -> None:
        self.received: ProviderGenerationInput | None = None
        self._delegate = FakeDocumentProvider()

    def generate(self, payload: ProviderGenerationInput):
        self.received = payload
        return self._delegate.generate(payload)


class DocumentGenerationCoreTests(unittest.TestCase):
    """Pure-core tests that do not touch the database."""

    def _request(self, mode: GenerationMode, parsed_extract: dict) -> DocumentGenerationRequest:
        facts = build_source_facts(
            record_type="control_narrative",
            parsed_extract=parsed_extract,
            source_snapshot_id="snap-1",
        )
        template = TemplateSpec(
            id=None,
            name="INTELLI default template",
            source="intelli_default",
            sections=["Commands", "Setpoints", "Related documents"],
        )
        return DocumentGenerationRequest(
            record_type="control_narrative",
            document_title="EM_TEST Control Narrative",
            mode=mode,
            module_name="EM_TEST",
            source_snapshot_id="snap-1",
            source_filename="EM_TEST.L5X",
            template=template,
            facts=facts,
        )

    def test_fake_provider_produces_predictable_output(self) -> None:
        payload = ProviderGenerationInput(
            system_prompt="sys",
            record_type="control_narrative",
            document_title="T",
            module_name="EM_TEST",
            template=TemplateSpec(None, "t", "intelli_default", ["Commands"]),
            sections=["Commands"],
            facts=[
                GenerationSourceFact(
                    key="commands",
                    label="Commands",
                    values=["MIX", "START"],
                    source_field="LogicSnapshot.parsed_extract.commands",
                    source_snapshot_id="snap-1",
                )
            ],
        )
        out_a = FakeDocumentProvider().generate(payload)
        out_b = FakeDocumentProvider().generate(payload)
        self.assertEqual(out_a.sections[0].body_markdown, out_b.sections[0].body_markdown)
        self.assertIn("MIX", out_a.sections[0].body_markdown)
        self.assertIn("START", out_a.sections[0].body_markdown)
        self.assertEqual(out_a.confidence, "medium")

    def test_provider_receives_only_structured_facts_not_prompt_soup(self) -> None:
        provider = _RecordingProvider()
        parsed_extract = {
            "commands": {"MIX": {"devices": ["AGITATOR_101"]}},
            "setpoints": ["mix_time_min=30"],
            # A raw blob that must never reach the provider verbatim.
            "raw_ladder_xml": "<RLL><Rung>XIC(Foo)OTE(Bar)</Rung></RLL>",
        }
        request = self._request(GenerationMode.LLM_ASSISTED, parsed_extract)
        generate_document(request, provider=provider)

        payload = provider.received
        self.assertIsInstance(payload, ProviderGenerationInput)
        # Provider only sees typed facts, each with provenance.
        for fact in payload.facts:
            self.assertIsInstance(fact, GenerationSourceFact)
            self.assertTrue(fact.source_field.startswith("LogicSnapshot.parsed_extract."))
        # The raw export blob is never handed over.
        self.assertNotIn("raw_ladder_xml", [f.key for f in payload.facts])
        for fact in payload.facts:
            for value in fact.values:
                self.assertNotIn("<RLL>", value)
        self.assertEqual(payload.sections, ["Commands", "Setpoints", "Related documents"])

    def test_missing_facts_become_needs_engineer_input(self) -> None:
        request = self._request(GenerationMode.LLM_ASSISTED, {"commands": {}})
        result = generate_document(request, provider=FakeDocumentProvider())
        self.assertIn("Needs engineer input", result.body_markdown)
        self.assertTrue(len(result.missing_facts) > 0)
        # Every missing fact retains its provenance for the engineer.
        for fact in result.missing_facts:
            self.assertFalse(fact.present)
            self.assertTrue(fact.source_field)

    def test_openai_provider_uses_responses_api_and_structured_facts(self) -> None:
        payload = ProviderGenerationInput(
            system_prompt="sys",
            record_type="control_narrative",
            document_title="EM_TEST Narrative",
            module_name="EM_TEST",
            template=TemplateSpec(None, "Template", "intelli_default", ["Commands"]),
            sections=["Commands"],
            facts=[
                GenerationSourceFact(
                    key="commands",
                    label="Commands",
                    values=["MIX"],
                    source_field="LogicSnapshot.parsed_extract.commands",
                    source_snapshot_id="snap-1",
                )
            ],
        )
        provider = OpenAIResponsesDocumentProvider(
            LLMConfig(
                provider_name="openai",
                enabled=True,
                model_name="gpt-test",
                temperature=0.2,
                max_tokens=512,
            ),
            api_key="test-key",
            base_url="https://api.test/v1",
        )

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return (
                    b'{"output_text":"{\\"sections\\":[{\\"title\\":\\"Commands\\",'
                    b'\\"body_markdown\\":\\"MIX is the available command.\\",'
                    b'\\"fact_keys\\":[\\"commands\\"],\\"needs_engineer_input\\":false}],'
                    b'\\"assumptions\\":[],\\"warnings\\":[],\\"confidence\\":\\"high\\"}"}'
                )

        def fake_urlopen(req, timeout):
            self.assertEqual(req.full_url, "https://api.test/v1/responses")
            self.assertEqual(req.headers["Authorization"], "Bearer test-key")
            body = json.loads(req.data.decode("utf-8"))
            self.assertEqual(body["model"], "gpt-test")
            self.assertIn("Return only valid JSON", body["instructions"])
            self.assertIn('"facts"', body["input"])
            self.assertNotIn("raw_ladder_xml", body["input"])
            self.assertEqual(timeout, 60)
            return FakeResponse()

        with patch("app.services.document_generation.providers.urllib_request.urlopen", side_effect=fake_urlopen):
            result = provider.generate(payload)

        self.assertEqual(result.provider_name, "openai")
        self.assertEqual(result.confidence, "high")
        self.assertEqual(result.sections[0].fact_keys, ["commands"])
        self.assertIn("MIX", result.sections[0].body_markdown)


class AiDraftPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "doc_gen_test.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{self._db_path}"
        reset_engine_cache()
        project_store.reset()
        init_db()
        self.db = get_session_factory()()
        FakeDocumentProvider.last_payload = None

    def tearDown(self) -> None:
        self.db.close()
        project_store.reset()
        reset_engine_cache()
        os.environ.pop("DATABASE_URL", None)
        self._tmpdir.cleanup()

    def _module_with_snapshot(self, parsed_extract: dict):
        unit = svc.create_process_unit(self.db, ProcessUnitCreate(name="AI Unit"))
        module = svc.create_equipment_module(
            self.db, unit.id, EquipmentModuleCreate(name="EM_AI")
        )
        svc.create_logic_snapshot(
            self.db,
            module.id,
            LogicSnapshotCreate(
                source_type=SnapshotSourceType.L5X,
                source_filename="EM_AI.L5X",
                parsed_extract=parsed_extract,
            ),
        )
        return unit, module

    def test_llm_assisted_draft_creates_revision_and_review_item(self) -> None:
        unit, module = self._module_with_snapshot(
            {"commands": {"MIX": {"devices": ["AGITATOR_101"]}}}
        )

        revision, result = svc.generate_ai_document_draft(
            self.db,
            module_id=module.id,
            record_type=EngineeringRecordType.CONTROL_NARRATIVE,
            generation_mode=GenerationMode.LLM_ASSISTED,
            provider=FakeDocumentProvider(),
        )

        self.assertEqual(revision.status, DocumentRevisionStatus.DRAFT)
        self.assertEqual(result.mode, GenerationMode.LLM_ASSISTED)
        self.assertEqual(result.provider_name, "fake")
        self.assertIn("AGITATOR_101", revision.body_markdown or "")
        # ReviewItem opened, never auto-approved.
        review_items = svc.list_review_items_for_process_unit(self.db, unit.id)
        self.assertEqual(len(review_items), 1)
        self.assertEqual(review_items[0].status, ReviewItemStatus.OPEN)
        self.assertEqual(review_items[0].engineering_record_id, revision.engineering_record_id)

    def test_provider_receives_structured_facts_via_wrapper(self) -> None:
        _unit, module = self._module_with_snapshot(
            {
                "commands": {"MIX": {"devices": ["AGITATOR_101"]}},
                "raw_ladder_xml": "<RLL>XIC(secret)</RLL>",
            }
        )
        provider = _RecordingProvider()
        svc.generate_ai_document_draft(
            self.db,
            module_id=module.id,
            record_type=EngineeringRecordType.CONTROL_NARRATIVE,
            generation_mode=GenerationMode.LLM_ASSISTED,
            provider=provider,
        )
        self.assertIsNotNone(provider.received)
        self.assertNotIn("raw_ladder_xml", [f.key for f in provider.received.facts])
        for fact in provider.received.facts:
            for value in fact.values:
                self.assertNotIn("<RLL>", value)

    def test_missing_facts_preserved_in_persisted_draft(self) -> None:
        _unit, module = self._module_with_snapshot({})
        revision, result = svc.generate_ai_document_draft(
            self.db,
            module_id=module.id,
            record_type=EngineeringRecordType.IO_LIST,
            generation_mode=GenerationMode.LLM_ASSISTED,
            provider=FakeDocumentProvider(),
        )
        self.assertIn("Needs engineer input", revision.body_markdown or "")
        self.assertTrue(len(result.missing_facts) > 0)
        self.assertTrue(
            len(revision.structured_content["missing_facts"]) > 0
        )

    def test_approved_revision_is_preserved(self) -> None:
        unit, module = self._module_with_snapshot(
            {"commands": {"START": {"steps": ["Open valve"]}}}
        )
        record = svc.create_engineering_record(
            self.db,
            EngineeringRecordCreate(
                process_unit_id=unit.id,
                module_id=module.id,
                record_type=EngineeringRecordType.CONTROL_NARRATIVE,
                title="EM_AI Control Narrative",
            ),
        )
        approved = svc.create_document_revision(
            self.db,
            record.id,
            DocumentRevisionCreate(
                revision="1.0",
                status=DocumentRevisionStatus.APPROVED,
                body_markdown="Approved baseline narrative.",
                created_by="eng",
                approved_by="lead",
            ),
        )

        revision, _result = svc.generate_ai_document_draft(
            self.db,
            record_id=record.id,
            generation_mode=GenerationMode.LLM_ASSISTED,
            provider=FakeDocumentProvider(),
        )

        revisions = svc.list_document_revisions(self.db, record.id)
        approved_now = [r for r in revisions if r.status == DocumentRevisionStatus.APPROVED]
        self.assertEqual([r.id for r in approved_now], [approved.id])
        self.assertEqual(approved_now[0].body_markdown, "Approved baseline narrative.")
        self.assertEqual(revision.status, DocumentRevisionStatus.DRAFT)
        # The approved revision is offered to the provider as context only.
        self.assertIsNotNone(FakeDocumentProvider.last_payload)
        self.assertIsNotNone(FakeDocumentProvider.last_payload.approved_revision_excerpt)

    def test_deterministic_mode_still_works(self) -> None:
        _unit, module = self._module_with_snapshot(
            {"alarms": ["HIGH_LEVEL"], "priorities": ["High"]}
        )
        revision, result = svc.generate_ai_document_draft(
            self.db,
            module_id=module.id,
            record_type=EngineeringRecordType.ALARM_RATIONALIZATION,
            generation_mode=GenerationMode.DETERMINISTIC_TEMPLATE,
        )
        self.assertEqual(result.mode, GenerationMode.DETERMINISTIC_TEMPLATE)
        self.assertEqual(revision.status, DocumentRevisionStatus.DRAFT)
        self.assertEqual(
            revision.structured_content["generation"], "deterministic_template"
        )
        self.assertIn("HIGH_LEVEL", revision.body_markdown or "")
        self.assertIn("facts_used", revision.structured_content)

    def test_selected_template_mismatch_conflicts(self) -> None:
        _unit, module = self._module_with_snapshot({"tags": ["MOTOR_RUN"]})
        template = svc.create_document_template(
            self.db,
            DocumentTemplateCreate(
                name="Narrative template",
                record_type=EngineeringRecordType.CONTROL_NARRATIVE,
                template_schema={"sections": ["Purpose"]},
            ),
        )
        with self.assertRaises(svc.ConflictError):
            svc.generate_ai_document_draft(
                self.db,
                module_id=module.id,
                record_type=EngineeringRecordType.IO_LIST,
                generation_mode=GenerationMode.LLM_ASSISTED,
                selected_template_id=template.id,
                provider=FakeDocumentProvider(),
            )


if __name__ == "__main__":
    unittest.main()
