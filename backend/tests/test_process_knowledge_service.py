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


if __name__ == "__main__":
    unittest.main()
