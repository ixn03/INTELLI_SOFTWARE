from app.models.control_model import (
    ControlController,
    ControlProgram,
    ControlProject,
    ControlRoutine,
    ControlTag,
)
from app.services.llm_orchestrator_service import (
    MockLlmProvider,
    answer_with_llm_assist,
)
from app.services.normalization_service import normalize_l5x_project


def _tiny_norm(question_tag: str = "Motor_Run") -> tuple[list, list, list]:
    proj = ControlProject(
        project_name="p",
        file_hash="llm-orch-test",
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


def test_llm_disabled_still_returns_deterministic_shape() -> None:
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


class _BadLlm:
    def complete(self, system: str, user: str) -> str:
        return "Problem is PhantomTag_XYZ not listed."


def test_hallucinated_tag_surfaces_warning() -> None:
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


def test_mock_llm_paraphrase_from_answer_line() -> None:
    cos, rels, ecs = _tiny_norm()
    llm = MockLlmProvider()
    # Mock reads ANSWER: prefix from user payload
    out = answer_with_llm_assist(
        "Motor_Run",
        cos,
        rels,
        ecs,
        enable_llm=True,
        llm=llm,
    )
    assert isinstance(out["answer"], str)
