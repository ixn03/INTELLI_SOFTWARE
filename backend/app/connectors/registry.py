from __future__ import annotations

from app.connectors.base import PlatformConnector
from app.connectors.deltav_fhx import DeltaVFHXConnector
from app.connectors.honeywell_experion import HoneywellExperionConnector
from app.connectors.rockwell_l5x import RockwellL5XConnector
from app.connectors.siemens_tia import SiemensTIAConnector


# Order is a deterministic tie-breaker when two connectors report the same confidence.
_CONNECTORS: list[PlatformConnector] = [
    RockwellL5XConnector(),
    DeltaVFHXConnector(),
    SiemensTIAConnector(),
    HoneywellExperionConnector(),
]


def connector_catalog() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for connector in _CONNECTORS:
        ext = getattr(connector, "supported_extensions", ()) or ()
        pv = getattr(connector, "parser_version", "0") or "0"
        rows.append(
            {
                "platform": connector.platform,
                "name": connector.display_name,
                "display_name": connector.display_name,
                "supported_extensions": ",".join(ext),
                "parser_version": str(pv),
            }
        )
    return rows


def get_connector(filename: str, content: bytes) -> PlatformConnector:
    ranked = sorted(
        (
            (idx, connector.can_parse(filename, content), connector)
            for idx, connector in enumerate(_CONNECTORS)
        ),
        key=lambda row: (-row[1].confidence, row[0]),
    )

    _idx, match, connector = ranked[0]

    if match.confidence <= 0:
        raise ValueError("No INTELLI connector recognized this file yet.")

    return connector
