from app.models.knowledge import KnowledgeItem, KnowledgeStatus, KnowledgeType
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
