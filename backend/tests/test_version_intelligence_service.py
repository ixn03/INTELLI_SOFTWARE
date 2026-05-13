from app.models.reasoning import (
    ControlObject,
    ControlObjectType,
    Relationship,
    RelationshipType,
)
from app.services.version_intelligence_service import analyze_version_impact


def _norm(reads_auto: bool) -> dict:
    objs = [
        ControlObject(
            id="tag::PLC/Pump_B_Run",
            name="Pump_B_Run",
            object_type=ControlObjectType.TAG,
        ),
        ControlObject(
            id="tag::PLC/AutoMode",
            name="AutoMode",
            object_type=ControlObjectType.TAG,
        ),
        ControlObject(
            id="rung::PLC/Main/Rung[1]",
            name="Rung[1]",
            object_type=ControlObjectType.RUNG,
        ),
    ]
    rels = [
        Relationship(
            source_id="rung::PLC/Main/Rung[1]",
            target_id="tag::PLC/Pump_B_Run",
            relationship_type=RelationshipType.WRITES,
            source_location="Main/Rung[1]",
            platform_specific={"instruction_type": "OTE"},
        )
    ]
    if reads_auto:
        rels.append(
            Relationship(
                source_id="rung::PLC/Main/Rung[1]",
                target_id="tag::PLC/AutoMode",
                relationship_type=RelationshipType.READS,
                source_location="Main/Rung[1]",
                platform_specific={"instruction_type": "XIC"},
            )
        )
    return {"control_objects": objs, "relationships": rels, "execution_contexts": []}


def test_version_intelligence_explains_changed_permissive() -> None:
    summary = analyze_version_impact(_norm(False), _norm(True))
    text = " ".join(summary.operationally_significant_changes)
    assert "AutoMode" in text
    assert summary.risk_level == "medium"
    assert summary.confidence >= 0.8


def test_version_intelligence_no_hallucinated_process_meaning_on_no_change() -> None:
    summary = analyze_version_impact(_norm(True), _norm(True))
    assert summary.operationally_significant_changes == []
    assert summary.risk_level == "low"
