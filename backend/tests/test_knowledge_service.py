from app.models.knowledge import (
    KnowledgeItem,
    KnowledgeStatus,
    KnowledgeType,
    KnowledgeVerification,
)
from app.models.reasoning import ConfidenceLevel
from app.services.knowledge_service import KnowledgeService, knowledge_rank_score


def test_create_list_patch_knowledge() -> None:
    svc = KnowledgeService()
    item = KnowledgeItem(
        knowledge_type=KnowledgeType.TAG_DESCRIPTION,
        statement="Pump interlock text",
        target_name="Pump_Run",
        status=KnowledgeStatus.PROPOSED,
    )
    svc.create(item)
    assert len(svc.list_all()) == 1
    patched = svc.patch(item.id, status=KnowledgeStatus.VERIFIED, verified_by="eng1")
    assert patched is not None
    assert patched.status == KnowledgeStatus.VERIFIED
    assert patched.verified_by == "eng1"


def test_rejected_fix_preserved() -> None:
    svc = KnowledgeService()
    item = KnowledgeItem(
        knowledge_type=KnowledgeType.REJECTED_FIX,
        statement="Bad idea",
        status=KnowledgeStatus.REJECTED,
    )
    svc.create(item)
    assert svc.get(item.id) is not None


def test_knowledge_rank_score_verified_first() -> None:
    a = KnowledgeItem(
        knowledge_type=KnowledgeType.ASSUMPTION,
        statement="x",
        status=KnowledgeStatus.PROPOSED,
        confidence=ConfidenceLevel.HIGH,
    )
    b = KnowledgeItem(
        knowledge_type=KnowledgeType.VERIFIED_FIX,
        statement="y",
        status=KnowledgeStatus.VERIFIED,
        confidence=ConfidenceLevel.MEDIUM,
    )
    assert knowledge_rank_score(b) > knowledge_rank_score(a)


def test_knowledge_verification_approve_reject_supersede() -> None:
    svc = KnowledgeService()
    item = KnowledgeItem(
        knowledge_type=KnowledgeType.OPERATOR_GUIDANCE,
        statement="Use local reset after clearing jam.",
        verification=KnowledgeVerification(plant_scope="Line 1"),
    )
    svc.create(item)

    approved = svc.approve(
        item.id,
        verified_by="engineer1",
        verification_reason="Confirmed during commissioning",
        equipment_scope="Conveyor",
    )
    assert approved is not None
    assert approved.status == KnowledgeStatus.VERIFIED
    assert approved.verification.verified_by == "engineer1"
    assert approved.verification.equipment_scope == "Conveyor"

    rejected = svc.reject(item.id, rejected_by="engineer2", verification_reason="Obsolete")
    assert rejected is not None
    assert rejected.status == KnowledgeStatus.REJECTED
    assert rejected.verification.rejected_by == "engineer2"

    superseded = svc.supersede(item.id, superseded_by="new-note", verification_reason="Procedure changed")
    assert superseded is not None
    assert superseded.status == KnowledgeStatus.SUPERSEDED
    assert superseded.verification.superseded_by == "new-note"


def test_version_specific_and_rejected_fix_preserved() -> None:
    item = KnowledgeItem(
        knowledge_type=KnowledgeType.VERSION_SPECIFIC_BEHAVIOR,
        statement="v2.1 changed fill timeout.",
        version_range=">=2.1 <2.3",
        status=KnowledgeStatus.REJECTED,
    )
    assert item.version_range == ">=2.1 <2.3"
    assert item.status == KnowledgeStatus.REJECTED
