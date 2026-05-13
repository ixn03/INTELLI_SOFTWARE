from app.models.reasoning import (
    ConfidenceLevel,
    ControlObject,
    ControlObjectType,
    Relationship,
    RelationshipType,
    TraceResult,
    TruthConclusion,
)
from app.services.evidence_service import build_runtime_evidence, build_trace_evidence
from app.services.sequence_semantics_service import analyze_sequence_semantics
from app.services.trustworthiness_service import assess_runtime_confidence, assess_trace_confidence


def _tag(tag_id: str, name: str) -> ControlObject:
    return ControlObject(
        id=tag_id,
        name=name,
        object_type=ControlObjectType.TAG,
        attributes={"data_type": "DINT" if name == "State" else "BOOL"},
    )


def test_trace_evidence_reports_unsupported_and_lowers_trust() -> None:
    trace = TraceResult(
        target_object_id="tag::PLC/State",
        summary="State has unsupported writer.",
        conclusions=[
            TruthConclusion(
                statement="Structured Text is too complex.",
                confidence=ConfidenceLevel.LOW,
                platform_specific={"trace_v2_kind": "st_too_complex"},
            )
        ],
        writer_relationships=[
            Relationship(
                source_id="stmt::1",
                target_id="tag::PLC/State",
                relationship_type=RelationshipType.WRITES,
            )
        ],
    )
    bundle = build_trace_evidence(trace)
    trust = assess_trace_confidence(trace)
    assert bundle.unsupported_evidence
    assert bundle.confidence < 0.78
    assert trust.unsupported_reasons


def test_runtime_evidence_prioritizes_runtime_but_missing_lowers_confidence() -> None:
    trace = TraceResult(
        target_object_id="tag::PLC/Motor_Run",
        summary="Runtime evaluated.",
        platform_specific={
            "runtime_snapshot_evaluated": True,
            "satisfied_conditions": [{"natural_language": "AutoMode is TRUE", "snapshot_key": "AutoMode"}],
            "missing_conditions": [{"natural_language": "StartPB is TRUE", "snapshot_key": "StartPB"}],
            "unsupported_conditions": [],
            "conflicts": [],
        },
    )
    bundle = build_runtime_evidence(trace, {"AutoMode": True})
    trust = assess_runtime_confidence(trace)
    assert bundle.supporting_evidence[0].runtime_snapshot_keys == ["AutoMode"]
    assert trust.missing_runtime_reasons
    assert trust.confidence_score < 0.9


def test_sequence_semantics_detects_waiting_fault_manual_and_runtime_state() -> None:
    state_id = "tag::PLC/State"
    start_id = "tag::PLC/StartPB"
    fault_id = "tag::PLC/Faulted"
    rung_id = "rung::PLC/Main/Rung[1]"
    instr_id = "instr::PLC/Main/Rung[1]/mov1"
    objs = [
        _tag(state_id, "State"),
        _tag(start_id, "StartPB"),
        _tag(fault_id, "Faulted"),
        ControlObject(
            id=instr_id,
            name="MOV",
            object_type=ControlObjectType.INSTRUCTION,
            parent_ids=[rung_id],
            attributes={"instruction_type": "MOV", "operands": ["10", "State"]},
            platform_specific={"instruction_local_id": "mov1"},
        ),
    ]
    rels = [
        Relationship(
            source_id=rung_id,
            target_id=start_id,
            relationship_type=RelationshipType.READS,
            source_location="Rung[1]",
            platform_specific={"instruction_type": "XIC", "instruction_id": "i1"},
        ),
        Relationship(
            source_id=rung_id,
            target_id=fault_id,
            relationship_type=RelationshipType.READS,
            source_location="Rung[1]",
            platform_specific={"instruction_type": "XIO", "instruction_id": "i2"},
        ),
        Relationship(
            source_id=rung_id,
            target_id=state_id,
            relationship_type=RelationshipType.WRITES,
            source_location="Start command waits for Timer1.DN and not Faulted",
            platform_specific={"instruction_type": "MOV", "instruction_id": "mov1"},
        ),
    ]
    summary = analyze_sequence_semantics(objs, rels, [], {"State": 10})
    assert summary.current_possible_states[0]["runtime_value"] == 10
    assert summary.transition_conditions
    assert summary.likely_waiting_conditions
    assert summary.fault_conditions
    assert summary.manual_override_conditions
