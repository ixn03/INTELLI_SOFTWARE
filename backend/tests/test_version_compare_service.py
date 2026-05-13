from app.models.control_model import (
    ControlController,
    ControlProgram,
    ControlProject,
    ControlRoutine,
    ControlTag,
)
from app.services.normalization_service import normalize_l5x_project
from app.services.version_compare_service import compare_projects


def _project(hash_suffix: str, tag_type: str) -> ControlProject:
    return ControlProject(
        project_name="p",
        file_hash=f"cmp-{hash_suffix}",
        controllers=[
            ControlController(
                name="C1",
                programs=[
                    ControlProgram(
                        name="P1",
                        tags=[
                            ControlTag(
                                name="T1",
                                data_type=tag_type,
                                platform_source="rockwell_l5x",
                            )
                        ],
                        routines=[
                            ControlRoutine(
                                name="R1",
                                language="ladder",
                                instructions=[],
                                raw_logic="XIC(A);",
                                metadata={"raw_logic_hash": f"h{hash_suffix}"},
                            )
                        ],
                    )
                ],
            )
        ],
    )


def test_compare_detects_tag_type_change() -> None:
    old = normalize_l5x_project(_project("old", "BOOL"))
    new = normalize_l5x_project(_project("new", "DINT"))
    diff = compare_projects(old, new)
    assert "data_type_change" in " ".join(diff.risk_flags)
    assert any(c["change"] == "tag_data_type_changed" for c in diff.changed_objects)


def test_compare_no_change_same_hash() -> None:
    p = _project("same", "BOOL")
    a = normalize_l5x_project(p)
    b = normalize_l5x_project(p)
    diff = compare_projects(a, b)
    assert "No structural differences" in diff.summary
