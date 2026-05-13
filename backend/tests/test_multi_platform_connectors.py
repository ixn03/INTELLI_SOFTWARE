"""Multi-platform connector registry and shell ingest."""

from __future__ import annotations

import pytest

from app.connectors.multi_vendor import (
    DeltaVFHXConnector,
    HoneywellExperionConnector,
    SiemensTIAConnector,
)
from app.connectors.registry import connector_catalog, get_connector
from app.connectors.rockwell_l5x import RockwellL5XConnector


def test_catalog_lists_all_connectors_with_parser_fields() -> None:
    cat = connector_catalog()
    platforms = {row["platform"] for row in cat}
    assert platforms >= {"rockwell", "siemens_tia", "deltav", "honeywell"}
    for row in cat:
        assert "parser_version" in row
        assert "supported_extensions" in row
        assert row["display_name"]


def test_rockwell_selected_by_l5x_extension() -> None:
    c = get_connector("Main.L5X", b"<RSLogix5000Content/>")
    assert isinstance(c, RockwellL5XConnector)


def test_rockwell_selected_by_rslogix_magic_without_extension() -> None:
    c = get_connector("blob.xml", b"<RSLogix5000Content/>")
    assert isinstance(c, RockwellL5XConnector)


def test_siemens_selected_by_zap_extension() -> None:
    c = get_connector("line1.zap18", b"PK\x03\x04" + b"x" * 100)
    assert isinstance(c, SiemensTIAConnector)


def test_siemens_selected_by_openness_xml() -> None:
    xml = b'<?xml version="1.0"?><root xmlns="http://www.siemens.com/automation/Openness">'
    c = get_connector("export.xml", xml + b"</root>")
    assert isinstance(c, SiemensTIAConnector)


def test_deltav_selected_by_fhx_extension() -> None:
    c = get_connector("plant.fhx", b"<?xml version='1.0'?><FHX/>")
    assert isinstance(c, DeltaVFHXConnector)


def test_deltav_selected_by_xml_signature_without_extension() -> None:
    body = b"<?xml version='1.0'?><ModuleClass Name='X'/><!-- deltav -->"
    c = get_connector("snippet.xml", body)
    assert isinstance(c, DeltaVFHXConnector)


def test_honeywell_selected_by_vendor_extension() -> None:
    c = get_connector("site.hwl", b"\x00binary")
    assert isinstance(c, HoneywellExperionConnector)


def test_honeywell_selected_by_experion_xml_markers() -> None:
    xml = b"<?xml version='1.0'?><ExperionPKS_Export/>"
    c = get_connector("pks.xml", xml)
    assert isinstance(c, HoneywellExperionConnector)


def test_unknown_bytes_raises() -> None:
    with pytest.raises(ValueError, match="No INTELLI connector"):
        get_connector("mystery.bin", b"\xff" * 20)


def test_siemens_parse_preserves_shell_only() -> None:
    p = SiemensTIAConnector().parse(
        "demo.zap18",
        b"PK\x03\x04" + b"not a real zap" * 5,
    )
    assert p.controllers[0].platform == "siemens_tia"
    r = p.controllers[0].programs[0].routines[0]
    assert r.parse_status == "preserved_only"
    assert r.language == "unknown"


def test_deltav_parse_preserves_raw_text() -> None:
    text = b"<?xml version='1.0'?><FHX><Node/></FHX>"
    p = DeltaVFHXConnector().parse("x.fhx", text)
    assert p.metadata.get("ingest_mode") == "preservation_shell"
    raw = p.controllers[0].programs[0].routines[0].raw_logic
    assert raw is not None
    assert "FHX" in raw


def test_registry_order_is_stable_len() -> None:
    assert len(connector_catalog()) == 4
