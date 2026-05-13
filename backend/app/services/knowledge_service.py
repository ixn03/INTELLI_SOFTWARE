"""In-memory knowledge store (v1).

Verified items rank above proposed; rejected fixes are retained, not deleted.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.models.knowledge import KnowledgeItem, KnowledgeStatus


class KnowledgeService:
    def __init__(self) -> None:
        self._items: dict[str, KnowledgeItem] = {}

    def create(self, item: KnowledgeItem) -> KnowledgeItem:
        self._items[item.id] = item
        return item

    def get(self, item_id: str) -> Optional[KnowledgeItem]:
        return self._items.get(item_id)

    def list_all(self) -> list[KnowledgeItem]:
        return list(self._items.values())

    def list_by_target(self, target_object_id: str) -> list[KnowledgeItem]:
        return [
            i
            for i in self._items.values()
            if i.target_object_id == target_object_id
        ]

    def patch(self, item_id: str, **fields: object) -> Optional[KnowledgeItem]:
        item = self._items.get(item_id)
        if item is None:
            return None
        data = item.model_dump()
        for k, v in fields.items():
            if k in data and v is not None:
                data[k] = v
        data["updated_at"] = datetime.now(timezone.utc)
        updated = KnowledgeItem.model_validate(data)
        self._items[item_id] = updated
        return updated

    def approve(
        self,
        item_id: str,
        *,
        verified_by: str,
        verification_reason: Optional[str] = None,
        plant_scope: Optional[str] = None,
        equipment_scope: Optional[str] = None,
    ) -> Optional[KnowledgeItem]:
        item = self._items.get(item_id)
        if item is None:
            return None
        verification = item.verification.model_copy(
            update={
                "verified_by": verified_by,
                "verification_reason": verification_reason,
                "plant_scope": plant_scope,
                "equipment_scope": equipment_scope,
            }
        )
        return self.patch(
            item_id,
            status=KnowledgeStatus.VERIFIED,
            verified_by=verified_by,
            verification=verification,
        )

    def reject(
        self,
        item_id: str,
        *,
        rejected_by: str,
        verification_reason: Optional[str] = None,
    ) -> Optional[KnowledgeItem]:
        item = self._items.get(item_id)
        if item is None:
            return None
        verification = item.verification.model_copy(
            update={
                "rejected_by": rejected_by,
                "verification_reason": verification_reason,
            }
        )
        return self.patch(
            item_id,
            status=KnowledgeStatus.REJECTED,
            rejected_by=rejected_by,
            verification=verification,
        )

    def supersede(
        self,
        item_id: str,
        *,
        superseded_by: str,
        verification_reason: Optional[str] = None,
    ) -> Optional[KnowledgeItem]:
        item = self._items.get(item_id)
        if item is None:
            return None
        verification = item.verification.model_copy(
            update={
                "superseded_by": superseded_by,
                "verification_reason": verification_reason,
            }
        )
        return self.patch(
            item_id,
            status=KnowledgeStatus.SUPERSEDED,
            superseded_by=superseded_by,
            verification=verification,
        )

    def reset(self) -> None:
        self._items.clear()


knowledge_service = KnowledgeService()


def knowledge_rank_score(item: KnowledgeItem) -> tuple[int, str]:
    """Higher tuple sorts first: verified > proposed > superseded > rejected."""

    order = {
        KnowledgeStatus.VERIFIED: 3,
        KnowledgeStatus.PROPOSED: 2,
        KnowledgeStatus.SUPERSEDED: 1,
        KnowledgeStatus.REJECTED: 0,
    }
    source_bonus = 1 if (item.verified_by or item.verification.verified_by) else 0
    return (order.get(item.status, 0), source_bonus, item.updated_at.isoformat())


__all__ = ["KnowledgeService", "knowledge_service", "knowledge_rank_score"]
