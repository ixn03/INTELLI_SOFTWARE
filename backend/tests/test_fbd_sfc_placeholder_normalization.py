"""FBD/SFC routines: no crash, LOW confidence, metadata preserved."""

from app.models.control_model import (
    ControlController,
    ControlInstruction,
    ControlProgram,
    ControlProject,
    ControlRoutine,
)
from app.models.reasoning import ConfidenceLevel
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
