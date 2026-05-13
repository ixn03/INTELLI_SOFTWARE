from pathlib import Path

from tools.fixture_inventory import build_inventory


def test_fixture_inventory_generates_coverage_report_shape() -> None:
    fixtures = Path(__file__).resolve().parent / "fixtures"
    report = build_inventory(fixtures)
    assert report["fixture_count"] >= 1
    assert "summary" in report
    first = report["fixtures"][0]
    assert "parse_success" in first
    assert "coverage_gaps" in first
