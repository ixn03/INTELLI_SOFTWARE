from app.connectors.base import PlatformConnector
from app.connectors.placeholders import UnsupportedConnector
from app.connectors.rockwell_l5x import RockwellL5XConnector


CONNECTORS: list[PlatformConnector] = [
    RockwellL5XConnector(),

    # Placeholders are intentionally strict for now.
    # Do not broadly match .xml or .csv yet because many vendors use those formats.
    UnsupportedConnector(
        "siemens_tia",
        "Siemens TIA Portal",
        (".zap16", ".zap17", ".zap18", ".zap19"),
    ),
    UnsupportedConnector(
        "honeywell",
        "Honeywell",
        (".hwl", ".hwh", ".hsc"),
    ),
    UnsupportedConnector(
        "deltav",
        "DeltaV",
        (".fhx",),
    ),
]


def connector_catalog() -> list[dict[str, str]]:
    return [
        {
            "platform": connector.platform,
            "name": connector.display_name,
        }
        for connector in CONNECTORS
    ]


def get_connector(filename: str, content: bytes) -> PlatformConnector:
    ranked = sorted(
        ((connector.can_parse(filename, content), connector) for connector in CONNECTORS),
        key=lambda item: item[0].confidence,
        reverse=True,
    )

    match, connector = ranked[0]

    if match.confidence <= 0:
        raise ValueError("No INTELLI connector recognized this file yet.")

    return connector
