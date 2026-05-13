from app.connectors.honeywell_experion import HoneywellExperionConnector
from app.connectors.registry import get_connector
from app.services.normalization_service import normalize_l5x_project


def test_honeywell_connector_selection() -> None:
    content = b"Experion C300 Control Builder export"
    assert isinstance(get_connector("honeywell_c300.txt", content), HoneywellExperionConnector)


def test_honeywell_unknown_like_file_preserved_no_crash() -> None:
    content = b"Experion\nPROJECT: Unit1\nUNRECOGNIZED SECTION\n"
    project = HoneywellExperionConnector().parse("experion.txt", content)
    routine = project.controllers[0].programs[0].routines[0]
    assert project.project_name == "Unit1"
    assert routine.parse_status == "preserved_only"
    assert routine.raw_logic


def test_honeywell_preserved_metadata_visible_in_normalized_graph() -> None:
    content = b"Experion\nCONTROL MODULE: CM_200\n"
    project = HoneywellExperionConnector().parse("experion.txt", content)
    graph = normalize_l5x_project(project)
    routine = next(obj for obj in graph["control_objects"] if obj.name == "CM_200")
    assert routine.source_platform == "honeywell"
    assert routine.platform_specific["parse_status"] == "preserved_only"
    assert routine.platform_specific["export_format_detected"] == "text"
    assert routine.platform_specific["raw_source_present"] is True
