"""LLM Assist v1 — deterministic-first pipeline tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.models.control_model import (
    ControlController,
    ControlProgram,
    ControlProject,
    ControlRoutine,
    ControlTag,
)
from app.models.knowledge import KnowledgeItem, KnowledgeStatus, KnowledgeType
from app.models.reasoning import ConfidenceLevel
from app.services.ask_v2_service import answer_question_v2
from app.services.knowledge_service import knowledge_service
from app.services.llm_assist_service import answer_with_llm_assist
from app.services.llm_providers import (
    DisabledLLMProvider,
    MockLLMProvider,
    engineering_paragraph_from_evidence,
    load_llm_config_from_env,
    resolve_llm_provider,
)
from app.services.normalization_service import normalize_l5x_project


def _tiny_norm(question_tag: str = "Motor_Run") -> tuple[list, list, list]:
    proj = ControlProject(
        project_name="p",
        file_hash="llm-assist-test",
        controllers=[
            ControlController(
                name="PLC01",
                programs=[
                    ControlProgram(
                        name="MainProgram",
                        tags=[
                            ControlTag(
                                name=question_tag,
                                data_type="BOOL",
                                platform_source="rockwell_l5x",
                            ),
                            ControlTag(
                                name="StartPB",
                                data_type="BOOL",
                                platform_source="rockwell_l5x",
                            ),
                        ],
                        routines=[
                            ControlRoutine(
                                name="Main",
                                language="ladder",
                                instructions=[],
                            )
                        ],
                    )
                ],
            )
        ],
    )
    n = normalize_l5x_project(proj)
    return n["control_objects"], n["relationships"], n["execution_contexts"]


@pytest.fixture(autouse=True)
def _reset_knowledge() -> None:
    knowledge_service.reset()
    yield
    knowledge_service.reset()


def test_llm_disabled_fallback_works() -> None:
    cos, rels, ecs = _tiny_norm()
    out = answer_with_llm_assist(
        "Why is Motor_Run false?",
        cos,
        rels,
        ecs,
        enable_llm=False,
        llm=None,
    )
    assert "answer" in out
    assert "evidence_used" in out
    assert "warnings" in out
    assert any("llm_assist_disabled" in w for w in out["warnings"])
    assert out["deterministic_result"] is not None


def test_mock_llm_receives_evidence_package_only() -> None:
    MockLLMProvider.last_evidence_package = None
    cos, rels, ecs = _tiny_norm()
    out = answer_with_llm_assist(
        "Why is Motor_Run false?",
        cos,
        rels,
        ecs,
        enable_llm=True,
        llm=MockLLMProvider(),
    )
    assert out["answer"].startswith("[Assist]")
    ev = MockLLMProvider.last_evidence_package
    assert ev is not None
    forbidden_keys = {"raw_ladder", "raw_st", "l5x", "xml", "rung_text", "project_dump"}
    assert not forbidden_keys.intersection({k.lower() for k in ev})
    assert "trace_summary" in ev or "deterministic_conclusions" in ev


def test_no_hallucinated_tags_flagged() -> None:
    class _BadLlm:
        def generate_answer(self, system_prompt: str, evidence_package: dict) -> str:
            return "Root cause is PhantomTag_XYZ999."

    cos, rels, ecs = _tiny_norm()
    out = answer_with_llm_assist(
        "Why is Motor_Run false?",
        cos,
        rels,
        ecs,
        enable_llm=True,
        llm=_BadLlm(),
    )
    joined = " ".join(out["warnings"])
    assert "PhantomTag" in joined or "unlisted" in joined


def test_runtime_diagnosis_includes_runtime_evidence_fields() -> None:
    cos, rels, ecs = _tiny_norm()
    out = answer_with_llm_assist(
        "Why is Motor_Run not running?",
        cos,
        rels,
        ecs,
        runtime_snapshot={"Motor_Run": False, "StartPB": True},
        enable_llm=False,
        llm=None,
    )
    ev = out["evidence_used"]
    assert ev.get("runtime_snapshot_present") is True
    ps = out["deterministic_result"].platform_specific or {}
    assert ps.get("runtime_evaluation_used") is True


def test_sequence_evidence_key_present_when_target_resolved() -> None:
    cos, rels, ecs = _tiny_norm()
    out = answer_with_llm_assist(
        "Motor_Run",
        cos,
        rels,
        ecs,
        enable_llm=False,
    )
    assert "sequence_summary" in out["evidence_used"]


def test_unsupported_evidence_surfaces_warning() -> None:
    cos, rels, ecs = _tiny_norm()
    base = answer_question_v2("Motor_Run", cos, rels, ecs)
    ps = dict(base.platform_specific or {})
    ps["unsupported_conditions"] = ["LIM three-operand (unsupported in v2)"]
    base.platform_specific = ps
    with patch("app.services.llm_assist_service.answer_question_v2", return_value=base):
        out = answer_with_llm_assist(
            "Motor_Run",
            cos,
            rels,
            ecs,
            enable_llm=False,
        )
    assert any(
        "unsupported" in w.lower() for w in out["warnings"]
    ) or out["evidence_used"].get("unsupported_conditions")


def test_missing_target_asks_clarification() -> None:
    cos, rels, ecs = _tiny_norm()
    out = answer_with_llm_assist(
        "Why is the process upset?",
        cos,
        rels,
        ecs,
        enable_llm=False,
    )
    assert out["evidence_used"].get("target_resolution") == "unresolved"
    assert out["confidence"] == "low"
    assert "not resolved" in out["answer"].lower() or "pick" in out["answer"].lower()


def test_ask_v2_unchanged() -> None:
    cos, rels, ecs = _tiny_norm()
    r = answer_question_v2("Motor_Run", cos, rels, ecs)
    assert r.target_object_id
    assert (r.platform_specific or {}).get("router_version") == "v2"


def test_knowledge_notes_in_evidence() -> None:
    cos, rels, ecs = _tiny_norm()
    tag_id = next(o.id for o in cos if getattr(o, "name", None) == "Motor_Run")
    knowledge_service.create(
        KnowledgeItem(
            knowledge_type=KnowledgeType.TROUBLESHOOTING_NOTE,
            statement="Motor_Run is interlocked with door switches.",
            target_object_id=tag_id,
            target_name="Motor_Run",
            source="test",
            confidence=ConfidenceLevel.HIGH,
            status=KnowledgeStatus.VERIFIED,
        )
    )
    out = answer_with_llm_assist(
        "Motor_Run",
        cos,
        rels,
        ecs,
        enable_llm=False,
    )
    notes = out["evidence_used"].get("knowledge_notes") or []
    assert any("interlocked" in str(n).lower() for n in notes)


def test_disabled_provider_matches_engineering_template() -> None:
    ev = {
        "target_resolution": "deterministic_match",
        "runtime_verdict": "blocked",
        "blocking_conditions": ["Faulted is TRUE"],
        "trace_summary": "Motor_Run is de-energized when Permit is false.",
    }
    t = DisabledLLMProvider().generate_answer("sys", ev)
    assert "blocked" in t.lower() or "Faulted" in t


def test_resolve_provider_respects_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_LLM_ASSIST", "false")
    cfg = load_llm_config_from_env()
    p = resolve_llm_provider(cfg)
    assert isinstance(p, DisabledLLMProvider)


def test_engineering_paragraph_unresolved_lists_candidates() -> None:
    t = engineering_paragraph_from_evidence(
        {
            "target_resolution": "unresolved",
            "suggested_target_candidates": ["Motor_Run", "StartPB"],
        }
    )
    assert "Motor_Run" in t


def test_ask_v3_context_style_and_trust_in_evidence() -> None:
    cos, rels, ecs = _tiny_norm()
    out = answer_with_llm_assist(
        "Why is Motor_Run not running?",
        cos,
        rels,
        ecs,
        runtime_snapshot={"Motor_Run": False},
        conversation_context={
            "current_selected_object": "Motor_Run",
            "last_discussed_state": "Fill",
        },
        answer_style="concise_operator",
        enable_llm=False,
    )
    ev = out["evidence_used"]
    assert ev["answer_style"] == "concise_operator"
    assert ev["conversation_context"]["current_selected_object"] == "Motor_Run"
    assert "trust_assessment" in ev
    assert "sequence_semantics" in ev
    assert out["deterministic_result"].platform_specific["ask_v3_answer_style"] == "concise_operator"


def test_runtime_questions_prioritize_runtime_evidence_bundle() -> None:
    cos, rels, ecs = _tiny_norm()
    out = answer_with_llm_assist(
        "Runtime why is Motor_Run false?",
        cos,
        rels,
        ecs,
        runtime_snapshot={"Motor_Run": False, "StartPB": True},
        enable_llm=False,
    )
    ev = out["evidence_used"]
    assert ev["runtime_evaluation_used"] is True
    assert ev.get("evidence_bundle") is not None
