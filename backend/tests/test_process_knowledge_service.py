"""Tests for the process-unit control knowledge system of record."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.db.models.process_knowledge import (  # noqa: E402
    ApprovalDecision,
    DocumentStatus,
    DocumentRevisionStatus,
    EngineeringRecordStatus,
    EngineeringRecordType,
    GovernanceArtifactType,
    ImportAcquisitionMode,
    IssueStatus,
    ReviewItemStatus,
    SnapshotSourceType,
)
from app.db.session import get_session_factory, init_db, reset_engine_cache  # noqa: E402
from app.schemas.process_knowledge import (  # noqa: E402
    ControlIssueCreate,
    ControlImportSourceCreate,
    ControlNarrativeCreate,
    DocumentRevisionCreate,
    DocumentTemplateCreate,
    EquipmentModuleCreate,
    EngineeringRecordCreate,
    GovernanceArtifactCreate,
    LogicSnapshotCreate,
    ProcessUnitCreate,
)
from app.services import process_knowledge_service as svc  # noqa: E402
from app.services.project_store import project_store  # noqa: E402
from main import app  # noqa: E402


class ProcessKnowledgeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "process_knowledge_test.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{self._db_path}"
        reset_engine_cache()
        project_store.reset()
        init_db()
        self.db = get_session_factory()()

    def tearDown(self) -> None:
        self.db.close()
        project_store.reset()
        reset_engine_cache()
        os.environ.pop("DATABASE_URL", None)
        self._tmpdir.cleanup()

    def test_module_record_collects_first_mvp_governance_objects(self) -> None:
        unit = svc.create_process_unit(
            self.db,
            ProcessUnitCreate(
                name="600B Coating Prep",
                area="Coating",
                description="Pilot process unit for control knowledge records.",
            ),
        )
        module = svc.create_equipment_module(
            self.db,
            unit.id,
            EquipmentModuleCreate(
                name="EM_COAT_PREP",
                module_type="DeltaV Equipment Module",
                aliases=["_EM_COAT_PREP"],
            ),
        )
        snapshot = svc.create_logic_snapshot(
            self.db,
            module.id,
            LogicSnapshotCreate(
                source_type=SnapshotSourceType.FHX,
                source_filename="EM_COAT_PREP.fhx",
                file_hash="abc123",
                parsed_extract={
                    "commands": {
                        "MIX": {
                            "steps": ["SETUP_MIX", "INIT_MIX", "MIXING", "MIXING_END"],
                            "devices": ["VFD_AGIT", "XV_DISCH", "PREP_PMP"],
                            "setpoints": {"mix_time_min": 30},
                        }
                    }
                },
                created_by="controls.engineer",
            ),
        )
        narrative = svc.create_control_narrative(
            self.db,
            module.id,
            ControlNarrativeCreate(
                snapshot_id=snapshot.id,
                title="EM_COAT_PREP control narrative",
                body_markdown="# EM_COAT_PREP\n\nMIX runs agitator for 30 minutes.",
                status=DocumentStatus.APPROVED,
                revision="1.0",
                approved_by="lead.ce",
            ),
        )
        io_list = svc.create_governance_artifact(
            self.db,
            module.id,
            GovernanceArtifactCreate(
                snapshot_id=snapshot.id,
                artifact_type=GovernanceArtifactType.IO_LIST,
                title="EM_COAT_PREP IO list",
                status=DocumentStatus.IN_REVIEW,
                content_json={"tags": ["VFD_AGIT", "XV_DISCH", "PREP_PMP"]},
            ),
        )
        cem = svc.create_governance_artifact(
            self.db,
            module.id,
            GovernanceArtifactCreate(
                snapshot_id=snapshot.id,
                artifact_type=GovernanceArtifactType.CEM,
                title="EM_COAT_PREP cause and effect",
                status=DocumentStatus.DRAFT,
                content_json={"rows": [{"cause": "NOT_IN_SPEC", "effect": "BLOCK_MIX"}]},
            ),
        )
        issue = svc.create_control_issue(
            self.db,
            module.id,
            ControlIssueCreate(
                found_in_snapshot_id=snapshot.id,
                title="MIX_COMPLETED set before OAR confirms",
                symptom="Batch completed flag may set before operator confirmation.",
                root_cause="Completion condition was evaluated at prompt start.",
                status=IssueStatus.ROOT_CAUSED,
                tags=["MIX", "MIX_COMPLETED", "OAR"],
                created_by="controls.engineer",
            ),
        )
        self.db.commit()

        record = svc.module_record(self.db, module.id)

        self.assertEqual(record["module"].name, "EM_COAT_PREP")
        self.assertEqual([s.id for s in record["snapshots"]], [snapshot.id])
        self.assertEqual([n.id for n in record["narratives"]], [narrative.id])
        self.assertEqual(
            {a.id for a in record["governance_artifacts"]},
            {io_list.id, cem.id},
        )
        self.assertEqual([i.id for i in record["issues"]], [issue.id])

    def test_duplicate_module_name_in_same_unit_conflicts(self) -> None:
        unit = svc.create_process_unit(self.db, ProcessUnitCreate(name="Unit 1"))
        payload = EquipmentModuleCreate(name="EM_SHARED")
        svc.create_equipment_module(self.db, unit.id, payload)
        with self.assertRaises(svc.ConflictError):
            svc.create_equipment_module(self.db, unit.id, payload)

    def test_engineering_record_template_and_approved_revision_are_created(self) -> None:
        unit = svc.create_process_unit(self.db, ProcessUnitCreate(name="Unit Docs"))
        module = svc.create_equipment_module(
            self.db,
            unit.id,
            EquipmentModuleCreate(name="EM_DOCS"),
        )
        record = svc.create_engineering_record(
            self.db,
            EngineeringRecordCreate(
                process_unit_id=unit.id,
                module_id=module.id,
                record_type=EngineeringRecordType.CONTROL_NARRATIVE,
                title="EM_DOCS narrative",
                owner="controls",
            ),
        )
        template = svc.create_document_template(
            self.db,
            DocumentTemplateCreate(
                name="Default EM narrative",
                record_type=EngineeringRecordType.CONTROL_NARRATIVE,
                template_body="# Purpose\n\n# Sequence\n",
                version="1.0",
            ),
        )
        revision = svc.create_document_revision(
            self.db,
            record.id,
            DocumentRevisionCreate(
                template_id=template.id,
                revision="1.0",
                status=DocumentRevisionStatus.APPROVED,
                body_markdown="Approved narrative body.",
                created_by="engineer1",
                approved_by="lead1",
            ),
        )
        self.db.commit()

        revisions = svc.list_document_revisions(self.db, record.id)
        self.assertEqual(record.record_type, EngineeringRecordType.CONTROL_NARRATIVE)
        self.assertEqual(template.active, True)
        self.assertEqual(revisions[0].id, revision.id)
        self.assertEqual(revisions[0].status, DocumentRevisionStatus.APPROVED)

    def test_import_sync_marks_governed_records_for_review_on_logic_change(self) -> None:
        unit = svc.create_process_unit(self.db, ProcessUnitCreate(name="Converting Area"))
        module = svc.create_equipment_module(
            self.db,
            unit.id,
            EquipmentModuleCreate(name="Filtrate Separator", aliases=["FILTRATE_SEP"]),
        )
        source = svc.create_import_source(
            self.db,
            ControlImportSourceCreate(
                process_unit_id=unit.id,
                module_id=module.id,
                name="Daily PLC XML export",
                source_system="rockwell_l5x",
                acquisition_mode=ImportAcquisitionMode.SCHEDULED_EXPORT,
                schedule="daily",
            ),
        )
        source_id = source.id
        module_id = module.id
        self.db.commit()

        fixtures = _BACKEND_ROOT / "tests" / "fixtures" / "l5x"
        first = fixtures / "Conveyance_LD.L5X"
        second = fixtures / "Array_Scroll.L5X"

        baseline = svc.run_import_sync_from_bytes(
            self.db,
            source_id=source_id,
            filename=first.name,
            content=first.read_bytes(),
            actor="controls.engineer",
        )
        self.assertEqual(baseline["run"].diff_summary, "Baseline import; no previous logic snapshot for this module.")

        narrative = svc.create_control_narrative(
            self.db,
            module_id,
            ControlNarrativeCreate(
                snapshot_id=baseline["snapshot"].id,
                title="Filtrate Separator narrative",
                body_markdown="Approved baseline narrative.",
                status=DocumentStatus.APPROVED,
            ),
        )
        io_list = svc.create_governance_artifact(
            self.db,
            module_id,
            GovernanceArtifactCreate(
                snapshot_id=baseline["snapshot"].id,
                artifact_type=GovernanceArtifactType.IO_LIST,
                title="Filtrate Separator IO list",
                status=DocumentStatus.APPROVED,
                content_json={"tags": ["Motor_Run"]},
            ),
        )
        cem = svc.create_governance_artifact(
            self.db,
            module_id,
            GovernanceArtifactCreate(
                snapshot_id=baseline["snapshot"].id,
                artifact_type=GovernanceArtifactType.CEM,
                title="Filtrate Separator CEM",
                status=DocumentStatus.APPROVED,
                content_json={"rows": []},
            ),
        )
        self.db.commit()

        changed = svc.run_import_sync_from_bytes(
            self.db,
            source_id=source_id,
            filename=second.name,
            content=second.read_bytes(),
            actor="controls.engineer",
        )

        self.assertEqual(changed["run"].status.value, "completed")
        self.assertIsNotNone(changed["diff"])
        self.assertGreater(len(changed["impacted_records"]), 0)
        self.assertEqual(narrative.status, DocumentStatus.NEEDS_REVIEW)
        self.assertEqual(io_list.status, DocumentStatus.NEEDS_REVIEW)
        self.assertEqual(cem.status, DocumentStatus.NEEDS_REVIEW)

    def test_import_sync_stores_richer_process_knowledge_extract(self) -> None:
        unit = svc.create_process_unit(self.db, ProcessUnitCreate(name="Extract Unit"))
        module = svc.create_equipment_module(
            self.db,
            unit.id,
            EquipmentModuleCreate(name="EM_EXTRACT"),
        )
        source = svc.create_import_source(
            self.db,
            ControlImportSourceCreate(
                process_unit_id=unit.id,
                module_id=module.id,
                name="Mini L5X upload",
                source_system="rockwell_l5x",
            ),
        )
        source_id = source.id
        self.db.commit()
        fixture = _BACKEND_ROOT / "tests" / "fixtures" / "l5x" / "mini_routine_mix.L5X"

        result = svc.run_import_sync_from_bytes(
            self.db,
            source_id=source_id,
            filename=fixture.name,
            content=fixture.read_bytes(),
            actor="controls.engineer",
        )
        extract = result["snapshot"].parsed_extract

        self.assertEqual(extract["connector"], "Rockwell Studio 5000 L5X")
        self.assertEqual(extract["project_name"], "Mini_Ctrl")
        self.assertIn("R_Ladder", extract["routines"])
        self.assertGreater(len(extract["tags"]), 0)
        self.assertTrue(extract["reads"] or extract["writes"] or extract["relationships"])
        self.assertTrue(extract["outputs"] or extract["devices"])
        self.assertIn("graph_summary", extract)
        self.assertIn("graph", extract)

    def test_generated_control_narrative_uses_richer_import_extract(self) -> None:
        unit = svc.create_process_unit(self.db, ProcessUnitCreate(name="Narrative Extract Unit"))
        module = svc.create_equipment_module(
            self.db,
            unit.id,
            EquipmentModuleCreate(name="EM_NARRATIVE_EXTRACT"),
        )
        source = svc.create_import_source(
            self.db,
            ControlImportSourceCreate(
                process_unit_id=unit.id,
                module_id=module.id,
                name="Mini L5X upload",
                source_system="rockwell_l5x",
            ),
        )
        source_id = source.id
        module_id = module.id
        self.db.commit()
        fixture = _BACKEND_ROOT / "tests" / "fixtures" / "l5x" / "mini_routine_mix.L5X"
        svc.run_import_sync_from_bytes(
            self.db,
            source_id=source_id,
            filename=fixture.name,
            content=fixture.read_bytes(),
            actor="controls.engineer",
        )

        revision = svc.generate_document_draft(
            self.db,
            module_id=module_id,
            record_type=EngineeringRecordType.CONTROL_NARRATIVE,
            actor="controls.engineer",
        )

        body = revision.body_markdown or ""
        self.assertIn("R_Ladder", body)
        self.assertIn("mini_routine_mix.L5X", body)
        useful_sections = [
            section
            for section in revision.structured_content["sections"]
            if section["facts"]
        ]
        self.assertGreaterEqual(len(useful_sections), 3)

    def test_changed_import_creates_logic_diff_review_items_and_preserves_approved_revision(self) -> None:
        unit = svc.create_process_unit(self.db, ProcessUnitCreate(name="Review Unit"))
        module = svc.create_equipment_module(
            self.db,
            unit.id,
            EquipmentModuleCreate(name="EM_REVIEW"),
        )
        source = svc.create_import_source(
            self.db,
            ControlImportSourceCreate(
                process_unit_id=unit.id,
                module_id=module.id,
                name="Daily XML sync",
                source_system="rockwell_l5x",
                acquisition_mode=ImportAcquisitionMode.SCHEDULED_EXPORT,
                schedule="daily",
            ),
        )
        record = svc.create_engineering_record(
            self.db,
            EngineeringRecordCreate(
                process_unit_id=unit.id,
                module_id=module.id,
                record_type=EngineeringRecordType.IO_LIST,
                title="EM_REVIEW IO list",
            ),
        )
        approved_revision = svc.create_document_revision(
            self.db,
            record.id,
            DocumentRevisionCreate(
                revision="1.0",
                status=DocumentRevisionStatus.APPROVED,
                structured_content={"tags": ["Old_Tag"]},
                created_by="engineer1",
                approved_by="lead1",
            ),
        )
        source_id = source.id
        record_id = record.id
        unit_id = unit.id
        self.db.commit()

        fixtures = _BACKEND_ROOT / "tests" / "fixtures" / "l5x"
        first = fixtures / "Conveyance_LD.L5X"
        second = fixtures / "Array_Scroll.L5X"

        baseline = svc.run_import_sync_from_bytes(
            self.db,
            source_id=source_id,
            filename=first.name,
            content=first.read_bytes(),
            actor="controls.engineer",
        )
        self.assertIsNone(baseline["logic_diff"])
        self.db.commit()

        changed = svc.run_import_sync_from_bytes(
            self.db,
            source_id=source_id,
            filename=second.name,
            content=second.read_bytes(),
            actor="controls.engineer",
        )
        logic_diff = changed["logic_diff"]
        self.assertIsNotNone(logic_diff)
        self.assertTrue(logic_diff.changed)
        self.assertIn(str(record_id), logic_diff.impacted_record_ids)
        self.assertGreater(len(changed["impacted_records"]), 0)
        self.assertEqual(changed["process_unit_id"], unit_id)
        self.assertEqual(changed["module_id"], module.id)
        self.assertEqual(changed["import_source_id"], source_id)
        self.assertEqual(changed["snapshot_id"], changed["snapshot"].id)
        self.assertEqual(changed["diff_id"], logic_diff.id)
        self.assertEqual(changed["changed"], True)
        self.assertEqual(changed["affected_record_ids"], [str(record_id)])
        self.assertEqual(len(changed["review_item_ids"]), 1)

        revisions = svc.list_document_revisions(self.db, record_id)
        self.assertEqual(len(revisions), 1)
        self.assertEqual(revisions[0].id, approved_revision.id)
        self.assertEqual(revisions[0].status, DocumentRevisionStatus.APPROVED)

        review_items = svc.list_review_items_for_process_unit(self.db, unit_id)
        self.assertEqual(len(review_items), 1)
        self.assertEqual(review_items[0].status, ReviewItemStatus.OPEN)

        _approved_item, approval = svc.approve_review_item(
            self.db,
            review_items[0].id,
            actor="lead.ce",
            comments="Accepted after review.",
        )
        self.assertEqual(approval.decision, ApprovalDecision.APPROVED)
        self.assertEqual(approval.engineering_record_id, record_id)

    def test_generate_draft_for_missing_control_narrative_creates_record_revision_and_review(self) -> None:
        unit = svc.create_process_unit(self.db, ProcessUnitCreate(name="Draft Unit"))
        module = svc.create_equipment_module(
            self.db,
            unit.id,
            EquipmentModuleCreate(name="Filtrate Separator"),
        )
        svc.create_logic_snapshot(
            self.db,
            module.id,
            LogicSnapshotCreate(
                source_type=SnapshotSourceType.L5X,
                source_filename="FILTRATE_SEPARATOR.L5X",
                parsed_extract={
                    "commands": {
                        "MIX": {
                            "steps": ["Start agitator", "Hold for mix timer"],
                            "devices": ["AGITATOR_101"],
                            "setpoints": {"mix_time_min": 30},
                        }
                    }
                },
            ),
        )

        revision = svc.generate_document_draft(
            self.db,
            module_id=module.id,
            record_type=EngineeringRecordType.CONTROL_NARRATIVE,
            actor="controls.engineer",
        )

        records, total = svc.list_engineering_records(
            self.db,
            module_id=module.id,
            record_type=EngineeringRecordType.CONTROL_NARRATIVE,
        )
        self.assertEqual(total, 1)
        self.assertEqual(revision.status, DocumentRevisionStatus.DRAFT)
        self.assertEqual(revision.engineering_record_id, records[0].id)
        self.assertIn("MIX", revision.body_markdown or "")
        self.assertIn("AGITATOR_101", revision.body_markdown or "")
        self.assertEqual(records[0].status, EngineeringRecordStatus.NEEDS_REVIEW)
        review_items = svc.list_review_items_for_process_unit(self.db, unit.id)
        self.assertEqual(len(review_items), 1)
        self.assertEqual(review_items[0].engineering_record_id, records[0].id)

    def test_generate_draft_uses_intelli_default_template_when_company_template_missing(self) -> None:
        unit = svc.create_process_unit(self.db, ProcessUnitCreate(name="Fallback Unit"))
        module = svc.create_equipment_module(
            self.db,
            unit.id,
            EquipmentModuleCreate(name="EM_FALLBACK"),
        )
        record = svc.create_engineering_record(
            self.db,
            EngineeringRecordCreate(
                process_unit_id=unit.id,
                module_id=module.id,
                record_type=EngineeringRecordType.ALARM_RATIONALIZATION,
                title="EM_FALLBACK Alarm Rationalization",
            ),
        )
        svc.create_logic_snapshot(
            self.db,
            module.id,
            LogicSnapshotCreate(
                source_type=SnapshotSourceType.XML,
                source_filename="EM_FALLBACK.xml",
                parsed_extract={"alarms": ["HIGH_LEVEL"], "priorities": ["High"]},
            ),
        )

        revision = svc.generate_document_draft(self.db, record_id=record.id)

        self.assertIsNone(revision.template_id)
        self.assertEqual(revision.structured_content["template"]["source"], "intelli_default")
        self.assertIn("Operator response", revision.body_markdown or "")

    def test_generate_draft_uses_active_template_and_parsed_extract_facts(self) -> None:
        unit = svc.create_process_unit(self.db, ProcessUnitCreate(name="Template Unit"))
        module = svc.create_equipment_module(
            self.db,
            unit.id,
            EquipmentModuleCreate(name="EM_TEMPLATE"),
        )
        record = svc.create_engineering_record(
            self.db,
            EngineeringRecordCreate(
                process_unit_id=unit.id,
                module_id=module.id,
                record_type=EngineeringRecordType.IO_LIST,
                title="EM_TEMPLATE IO List",
            ),
        )
        template = svc.create_document_template(
            self.db,
            DocumentTemplateCreate(
                name="Company IO List",
                record_type=EngineeringRecordType.IO_LIST,
                template_schema={"sections": ["Tag", "Direction", "Data type", "Source"]},
                active=True,
            ),
        )
        svc.create_logic_snapshot(
            self.db,
            module.id,
            LogicSnapshotCreate(
                source_type=SnapshotSourceType.L5X,
                source_filename="EM_TEMPLATE.L5X",
                parsed_extract={
                    "tags": ["MOTOR_RUN", "MOTOR_FAULT"],
                    "directions": ["Output", "Input"],
                    "data_types": ["BOOL"],
                },
            ),
        )

        revision = svc.generate_document_draft(self.db, record_id=record.id)

        self.assertEqual(revision.template_id, template.id)
        self.assertEqual(revision.structured_content["template"]["source"], "company")
        self.assertIn("MOTOR_RUN", revision.body_markdown or "")
        self.assertIn("Output", revision.body_markdown or "")
        self.assertIn("BOOL", revision.body_markdown or "")

    def test_generate_draft_marks_missing_facts_as_needs_engineer_input(self) -> None:
        unit = svc.create_process_unit(self.db, ProcessUnitCreate(name="Sparse Unit"))
        module = svc.create_equipment_module(
            self.db,
            unit.id,
            EquipmentModuleCreate(name="EM_SPARSE"),
        )
        record = svc.create_engineering_record(
            self.db,
            EngineeringRecordCreate(
                process_unit_id=unit.id,
                module_id=module.id,
                record_type=EngineeringRecordType.CAUSE_EFFECT_MATRIX,
                title="EM_SPARSE C&E",
            ),
        )
        svc.create_logic_snapshot(
            self.db,
            module.id,
            LogicSnapshotCreate(
                source_type=SnapshotSourceType.XML,
                source_filename="EM_SPARSE.xml",
                parsed_extract={},
            ),
        )

        revision = svc.generate_document_draft(self.db, record_id=record.id)

        self.assertIn("Needs engineer input", revision.body_markdown or "")
        missing_sections = [
            section
            for section in revision.structured_content["sections"]
            if section["needs_engineer_input"]
        ]
        self.assertGreater(len(missing_sections), 0)

    def test_generate_proposed_revision_preserves_approved_revision(self) -> None:
        unit = svc.create_process_unit(self.db, ProcessUnitCreate(name="Proposed Unit"))
        module = svc.create_equipment_module(
            self.db,
            unit.id,
            EquipmentModuleCreate(name="EM_PROPOSED"),
        )
        record = svc.create_engineering_record(
            self.db,
            EngineeringRecordCreate(
                process_unit_id=unit.id,
                module_id=module.id,
                record_type=EngineeringRecordType.CONTROL_NARRATIVE,
                title="EM_PROPOSED Control Narrative",
            ),
        )
        approved = svc.create_document_revision(
            self.db,
            record.id,
            DocumentRevisionCreate(
                revision="1.0",
                status=DocumentRevisionStatus.APPROVED,
                body_markdown="Approved baseline narrative.",
                created_by="controls.engineer",
                approved_by="lead.ce",
            ),
        )
        svc.create_logic_snapshot(
            self.db,
            module.id,
            LogicSnapshotCreate(
                source_type=SnapshotSourceType.L5X,
                source_filename="EM_PROPOSED.L5X",
                parsed_extract={"commands": {"START": {"steps": ["Open valve", "Start pump"]}}},
            ),
        )

        proposed = svc.generate_proposed_revision(self.db, record_id=record.id)

        revisions = svc.list_document_revisions(self.db, record.id)
        approved_revisions = [
            item for item in revisions if item.status == DocumentRevisionStatus.APPROVED
        ]
        proposed_revisions = [
            item for item in revisions if item.status == DocumentRevisionStatus.PROPOSED
        ]
        self.assertEqual([item.id for item in approved_revisions], [approved.id])
        self.assertEqual([item.id for item in proposed_revisions], [proposed.id])
        self.assertEqual(proposed.structured_content["base_revision_id"], str(approved.id))
        review_items = svc.list_review_items_for_process_unit(self.db, unit.id)
        self.assertEqual(len(review_items), 1)
        self.assertIsNotNone(review_items[0].proposed_document_update_id)

    def test_rejecting_review_item_records_approval_history(self) -> None:
        unit = svc.create_process_unit(self.db, ProcessUnitCreate(name="Reject Unit"))
        module = svc.create_equipment_module(
            self.db,
            unit.id,
            EquipmentModuleCreate(name="EM_REJECT"),
        )
        record = svc.create_engineering_record(
            self.db,
            EngineeringRecordCreate(
                process_unit_id=unit.id,
                module_id=module.id,
                record_type=EngineeringRecordType.CAUSE_EFFECT_MATRIX,
                title="EM_REJECT CEM",
                status=EngineeringRecordStatus.NEEDS_REVIEW,
            ),
        )
        snapshot = svc.create_logic_snapshot(
            self.db,
            module.id,
            LogicSnapshotCreate(
                source_type=SnapshotSourceType.L5X,
                source_filename="baseline.L5X",
                raw_project_id="baseline",
            ),
        )
        # Use the import-sync helper path for real review creation in other tests;
        # this direct record covers reject behavior without another fixture import.
        from app.db.models.process_knowledge import ReviewItem

        review = ReviewItem(
            process_unit_id=unit.id,
            module_id=module.id,
            engineering_record_id=record.id,
            logic_snapshot_id=snapshot.id,
            title="Review rejected CEM change",
            reason="Test rejection.",
            status=ReviewItemStatus.OPEN,
        )
        self.db.add(review)
        self.db.flush()

        _rejected_item, history = svc.reject_review_item(
            self.db,
            review.id,
            actor="lead.ce",
            comments="Not enough evidence.",
        )

        self.assertEqual(history.decision, ApprovalDecision.REJECTED)
        self.assertEqual(history.comments, "Not enough evidence.")

    def test_delete_process_unit_requires_empty_unit(self) -> None:
        unit = svc.create_process_unit(self.db, ProcessUnitCreate(name="Unit Delete"))
        module = svc.create_equipment_module(
            self.db,
            unit.id,
            EquipmentModuleCreate(name="EM_DELETE"),
        )

        with self.assertRaises(svc.ConflictError):
            svc.delete_process_unit_if_empty(self.db, unit.id)

        svc.delete_equipment_module(self.db, module.id)
        svc.delete_process_unit_if_empty(self.db, unit.id)
        self.assertIsNone(self.db.get(type(unit), unit.id))

    def test_delete_equipment_module_rejects_approved_revisions(self) -> None:
        unit = svc.create_process_unit(self.db, ProcessUnitCreate(name="Unit Approved Delete"))
        module = svc.create_equipment_module(
            self.db,
            unit.id,
            EquipmentModuleCreate(name="EM_APPROVED_DELETE"),
        )
        record = svc.create_engineering_record(
            self.db,
            EngineeringRecordCreate(
                process_unit_id=unit.id,
                module_id=module.id,
                record_type=EngineeringRecordType.CONTROL_NARRATIVE,
                title="Approved delete narrative",
            ),
        )
        svc.create_document_revision(
            self.db,
            record.id,
            DocumentRevisionCreate(
                revision="1.0",
                status=DocumentRevisionStatus.APPROVED,
                body_markdown="Approved body.",
            ),
        )

        with self.assertRaises(svc.ConflictError):
            svc.delete_equipment_module(self.db, module.id)

    def test_delete_document_revision_allows_draft_but_not_approved(self) -> None:
        unit = svc.create_process_unit(self.db, ProcessUnitCreate(name="Unit Revision Delete"))
        module = svc.create_equipment_module(
            self.db,
            unit.id,
            EquipmentModuleCreate(name="EM_REVISION_DELETE"),
        )
        record = svc.create_engineering_record(
            self.db,
            EngineeringRecordCreate(
                process_unit_id=unit.id,
                module_id=module.id,
                record_type=EngineeringRecordType.IO_LIST,
                title="Revision delete IO list",
            ),
        )
        draft = svc.create_document_revision(
            self.db,
            record.id,
            DocumentRevisionCreate(
                revision="0.1",
                status=DocumentRevisionStatus.DRAFT,
                body_markdown="Draft body.",
            ),
        )
        approved = svc.create_document_revision(
            self.db,
            record.id,
            DocumentRevisionCreate(
                revision="1.0",
                status=DocumentRevisionStatus.APPROVED,
                body_markdown="Approved body.",
            ),
        )

        svc.delete_document_revision(self.db, draft.id)
        self.assertIsNone(self.db.get(type(draft), draft.id))
        with self.assertRaises(svc.ConflictError):
            svc.delete_document_revision(self.db, approved.id)

    def test_reset_generated_workspace_data_preserves_approved_revisions(self) -> None:
        unit = svc.create_process_unit(self.db, ProcessUnitCreate(name="Unit Reset"))
        module = svc.create_equipment_module(
            self.db,
            unit.id,
            EquipmentModuleCreate(name="EM_RESET"),
        )
        record = svc.create_engineering_record(
            self.db,
            EngineeringRecordCreate(
                process_unit_id=unit.id,
                module_id=module.id,
                record_type=EngineeringRecordType.ALARM_RATIONALIZATION,
                title="Reset alarm rationalization",
            ),
        )
        draft = svc.create_document_revision(
            self.db,
            record.id,
            DocumentRevisionCreate(
                revision="0.1",
                status=DocumentRevisionStatus.DRAFT,
                body_markdown="Draft body.",
            ),
        )
        approved = svc.create_document_revision(
            self.db,
            record.id,
            DocumentRevisionCreate(
                revision="1.0",
                status=DocumentRevisionStatus.APPROVED,
                body_markdown="Approved body.",
            ),
        )

        result = svc.reset_generated_workspace_data(self.db)

        self.assertEqual(result["revisions_deleted"], 1)
        self.assertIsNone(self.db.get(type(draft), draft.id))
        self.assertIsNotNone(self.db.get(type(approved), approved.id))


    def test_seed_demo_process_unit_bootstraps_usable_demo(self) -> None:
        result = svc.seed_demo_process_unit(self.db)
        self.db.commit()

        self.assertIn("Demo", result["process_unit_name"])
        self.assertIn("Demo", result["module_name"])

        units, _ = svc.list_process_units(self.db)
        self.assertEqual(len(units), 1)

        # The seeded snapshot should let deterministic generation produce
        # meaningful (non-empty) draft content.
        revision = svc.generate_document_draft(
            self.db,
            module_id=result["module_id"],
            record_type=EngineeringRecordType.CONTROL_NARRATIVE,
            actor="controls.engineer",
        )
        body = revision.body_markdown or ""
        self.assertIn("VFD_AGIT", body)
        useful_sections = [
            section
            for section in revision.structured_content["sections"]
            if section["facts"]
        ]
        self.assertGreaterEqual(len(useful_sections), 3)

    def test_seed_demo_process_unit_is_non_destructive(self) -> None:
        first = svc.seed_demo_process_unit(self.db)
        second = svc.seed_demo_process_unit(self.db)
        self.db.commit()

        self.assertNotEqual(first["process_unit_id"], second["process_unit_id"])
        units, _ = svc.list_process_units(self.db)
        self.assertEqual(len(units), 2)

    def test_approving_review_item_promotes_draft_revision(self) -> None:
        unit = svc.create_process_unit(self.db, ProcessUnitCreate(name="Approve Promote Unit"))
        module = svc.create_equipment_module(
            self.db,
            unit.id,
            EquipmentModuleCreate(name="EM_APPROVE_PROMOTE"),
        )
        svc.create_logic_snapshot(
            self.db,
            module.id,
            LogicSnapshotCreate(
                source_type=SnapshotSourceType.L5X,
                source_filename="EM_APPROVE_PROMOTE.L5X",
                parsed_extract={"tags": ["MOTOR_RUN"], "alarms": ["HIGH_LEVEL"]},
            ),
        )
        revision = svc.generate_document_draft(
            self.db,
            module_id=module.id,
            record_type=EngineeringRecordType.IO_LIST,
            actor="controls.engineer",
        )
        self.assertEqual(revision.status, DocumentRevisionStatus.DRAFT)

        review_items = svc.list_review_items_for_process_unit(self.db, unit.id)
        self.assertEqual(len(review_items), 1)

        svc.approve_review_item(
            self.db,
            review_items[0].id,
            actor="lead.ce",
            comments="Looks good.",
        )

        self.db.refresh(revision)
        self.assertEqual(revision.status, DocumentRevisionStatus.APPROVED)
        self.assertEqual(revision.approved_by, "lead.ce")
        self.assertEqual(revision.engineering_record.status, EngineeringRecordStatus.ACTIVE)

    def test_rejecting_review_item_rejects_draft_revision(self) -> None:
        unit = svc.create_process_unit(self.db, ProcessUnitCreate(name="Reject Promote Unit"))
        module = svc.create_equipment_module(
            self.db,
            unit.id,
            EquipmentModuleCreate(name="EM_REJECT_PROMOTE"),
        )
        svc.create_logic_snapshot(
            self.db,
            module.id,
            LogicSnapshotCreate(
                source_type=SnapshotSourceType.L5X,
                source_filename="EM_REJECT_PROMOTE.L5X",
                parsed_extract={"tags": ["MOTOR_RUN"]},
            ),
        )
        revision = svc.generate_document_draft(
            self.db,
            module_id=module.id,
            record_type=EngineeringRecordType.IO_LIST,
            actor="controls.engineer",
        )
        review_items = svc.list_review_items_for_process_unit(self.db, unit.id)

        svc.reject_review_item(
            self.db,
            review_items[0].id,
            actor="lead.ce",
            comments="Needs rework.",
        )

        self.db.refresh(revision)
        self.assertEqual(revision.status, DocumentRevisionStatus.REJECTED)
        self.assertEqual(
            revision.engineering_record.status, EngineeringRecordStatus.NEEDING_SETUP
        )


class ProcessKnowledgeGenerateDraftApiTests(unittest.TestCase):
    """HTTP-level tests for module generate-ai-draft routes."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "generate_draft_api_test.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{self._db_path}"
        reset_engine_cache()
        project_store.reset()
        init_db()
        self.db = get_session_factory()()
        unit = svc.create_process_unit(self.db, ProcessUnitCreate(name="API Draft Unit"))
        module = svc.create_equipment_module(
            self.db,
            unit.id,
            EquipmentModuleCreate(name="EM_API_DRAFT"),
        )
        svc.create_logic_snapshot(
            self.db,
            module.id,
            LogicSnapshotCreate(
                source_type=SnapshotSourceType.L5X,
                source_filename="EM_API_DRAFT.L5X",
                parsed_extract={"tags": ["MIX_TIMER"], "directions": ["Output"]},
            ),
        )
        self.unit_id = unit.id
        self.module_id = module.id
        self.db.commit()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.db.close()
        project_store.reset()
        reset_engine_cache()
        os.environ.pop("DATABASE_URL", None)
        self._tmpdir.cleanup()

    def test_module_generate_ai_draft_creates_revision_and_review_item(self) -> None:
        response = self.client.post(
            f"/api/modules/{self.module_id}/engineering-records/control_narrative/generate-ai-draft",
            json={
                "generation_mode": "deterministic_template",
                "user_notes": None,
                "actor": "controls.engineer",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        self.assertEqual(payload["revision"]["status"], "draft")
        self.assertTrue(payload["revision"]["id"])
        self.assertEqual(payload["generation_mode"], "deterministic_template")

        records = self.client.get(
            "/api/engineering-records",
            params={"module_id": str(self.module_id), "record_type": "control_narrative"},
        )
        self.assertEqual(records.status_code, 200)
        self.assertEqual(len(records.json()), 1)

        revisions = self.client.get(
            f"/api/engineering-records/{records.json()[0]['id']}/revisions",
        )
        self.assertEqual(revisions.status_code, 200)
        self.assertEqual(len(revisions.json()), 1)
        self.assertEqual(revisions.json()[0]["status"], "draft")

        reviews = self.client.get(f"/api/process-units/{self.unit_id}/review-items")
        self.assertEqual(reviews.status_code, 200)
        matching = [
            item
            for item in reviews.json()
            if item["engineering_record_id"] == records.json()[0]["id"]
        ]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["status"], "open")


if __name__ == "__main__":
    unittest.main()
