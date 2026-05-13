from app.connectors.deltav_fhx import DeltaVFHXConnector, decode_fhx_text
from app.connectors.registry import get_connector
from app.models.reasoning import ControlObjectType
from app.services.normalization_service import normalize_l5x_project


FHX_TEXT = """SYSTEM "BatchSystem"
AREA "Reactor"
CONTROL_MODULE "CM101"
FUNCTION_BLOCK "AI1" TYPE "AI"
PARAMETER "OUT" := "PV101"
LINK "AI1.OUT" -> "PID1.PV"
FUNCTION_BLOCK "PID1" TYPE "PID"
PARAMETER "SP" := "SP101"
"""


def test_deltav_fhx_detection() -> None:
    assert isinstance(get_connector("plant.fhx", FHX_TEXT.encode()), DeltaVFHXConnector)


def test_deltav_utf16_decoding() -> None:
    encoded = b"\xff\xfe" + FHX_TEXT.encode("utf-16-le")
    text, encoding = decode_fhx_text(encoded)
    assert encoding == "utf-16-le"
    assert "CONTROL_MODULE" in text


def test_deltav_module_and_function_block_inventory_extraction() -> None:
    project = DeltaVFHXConnector().parse("plant.fhx", FHX_TEXT.encode())
    routine = project.controllers[0].programs[0].routines[0]
    assert project.project_name == "BatchSystem"
    assert routine.name == "CM101"
    assert routine.raw_logic and "FUNCTION_BLOCK" in routine.raw_logic
    assert [inst.metadata["block_name"] for inst in routine.instructions] == ["AI1", "PID1"]

    graph = normalize_l5x_project(project)
    fbs = [
        obj
        for obj in graph["control_objects"]
        if obj.object_type == ControlObjectType.FUNCTION_BLOCK
    ]
    assert len(fbs) == 2
    assert fbs[0].platform_specific["parameters"]


def test_deltav_unknown_sections_preserved_and_partial_no_crash() -> None:
    project = DeltaVFHXConnector().parse("partial.fhx", b"FHX\nODD_SECTION X\n")
    routine = project.controllers[0].programs[0].routines[0]
    assert routine.parse_status == "preserved_only"
    assert routine.raw_logic
