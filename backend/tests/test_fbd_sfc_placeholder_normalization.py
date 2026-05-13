"""FBD/SFC routines: no crash, LOW confidence, metadata preserved."""

from app.models.control_model import (
    ControlController,
    ControlInstruction,
    ControlProgram,
    ControlProject,
    ControlRoutine,
)
from app.models.reasoning import ConfidenceLevel
from app.models.reasoning import ControlObjectType, RelationshipType
from app.services.normalization_service import normalize_l5x_project


def _minimal_project(
    *,
    routine_lang: str,
    routine_name: str = "FBD_Routine",
) -> ControlProject:
    return ControlProject(
        project_name="p",
        file_hash="hash-fbd-sfc-test",
        controllers=[
            ControlController(
                name="PLC01",
                programs=[
                    ControlProgram(
                        name="MainProgram",
                        routines=[
                            ControlRoutine(
                                name=routine_name,
                                language=routine_lang,  # type: ignore[arg-type]
                                raw_logic="(* placeholder *)",
                                instructions=[
                                    ControlInstruction(
                                        instruction_type="FB_DUMMY",
                                        operands=["In1"],
                                        raw_text="FB_DUMMY(In1);",
                                        language="unknown",
                                    )
                                ],
                                metadata={"revision": "1.0"},
                            )
                        ],
                    )
                ],
            )
        ],
    )


def test_unsupported_fbd_routine_does_not_crash() -> None:
    out = normalize_l5x_project(_minimal_project(routine_lang="function_block"))
    routines = [o for o in out["control_objects"] if o.name == "FBD_Routine"]
    assert len(routines) == 1
    r = routines[0]
    assert r.confidence == ConfidenceLevel.LOW
    assert r.platform_specific.get("parse_status") == "unsupported_language"
    assert r.platform_specific.get("raw_logic_present") is True
    assert r.source_location.endswith("/Routine:FBD_Routine")
    assert r.attributes.get("language") == "function_block"
    meta = out["normalization_metadata"]
    assert "unsupported_ladder_instruction_inventory" in meta


def test_unsupported_sfc_routine_does_not_crash() -> None:
    proj = _minimal_project(routine_lang="sfc", routine_name="Seq_Main")
    out = normalize_l5x_project(proj)
    routines = [o for o in out["control_objects"] if o.name == "Seq_Main"]
    assert len(routines) == 1
    r = routines[0]
    assert r.confidence == ConfidenceLevel.LOW
    assert r.platform_specific.get("parse_status") == "unsupported_language"
    assert (r.platform_specific.get("rockwell_metadata") or {}).get("revision") == "1.0"


def test_normalized_objects_preserve_routine_metadata() -> None:
    out = normalize_l5x_project(_minimal_project(routine_lang="function_block"))
    r = next(o for o in out["control_objects"] if o.name == "FBD_Routine")
    assert (r.platform_specific.get("rockwell_metadata") or {}).get("revision") == "1.0"


def test_fbd_function_block_placeholder_object_and_connects_edge() -> None:
    project = _minimal_project(routine_lang="function_block")
    inst = project.controllers[0].programs[0].routines[0].instructions[0]
    inst.language = "fbd"
    inst.metadata = {
        "object_subtype": "function_block",
        "block_type": "TON",
        "block_name": "DelayTimer",
        "parameters": {"IN": "StartPB"},
        "connects_to": ["tag::PLC01.MainProgram.TimerDone"],
    }
    out = normalize_l5x_project(project)
    fb = next(
        o
        for o in out["control_objects"]
        if o.object_type == ControlObjectType.FUNCTION_BLOCK
    )
    assert fb.attributes["object_subtype"] == "function_block"
    assert fb.platform_specific["block_type"] == "TON"
    assert any(
        rel.relationship_type == RelationshipType.CONNECTS
        for rel in out["relationships"]
    )


def test_sfc_step_transition_action_placeholders() -> None:
    project = _minimal_project(routine_lang="sfc", routine_name="Seq_Main")
    routine = project.controllers[0].programs[0].routines[0]
    routine.instructions = [
        ControlInstruction(
            instruction_type="Step_1",
            language="sfc",
            id="step1",
            metadata={"object_subtype": "sfc_step", "sequences_to": ["trans1"]},
        ),
        ControlInstruction(
            instruction_type="Trans_1",
            language="sfc",
            id="trans1",
            metadata={"object_subtype": "sfc_transition", "condition_for": ["step2"]},
        ),
        ControlInstruction(
            instruction_type="Action_1",
            language="sfc",
            id="act1",
            metadata={"object_subtype": "sfc_action", "action_of": ["step1"]},
        ),
    ]
    out = normalize_l5x_project(project)
    object_types = {obj.object_type for obj in out["control_objects"]}
    rel_types = {rel.relationship_type for rel in out["relationships"]}
    assert ControlObjectType.SFC_STEP in object_types
    assert ControlObjectType.SFC_TRANSITION in object_types
    assert ControlObjectType.SFC_ACTION in object_types
    assert RelationshipType.SEQUENCES in rel_types
    assert RelationshipType.CONDITION_FOR in rel_types
    assert RelationshipType.ACTION_OF in rel_types
