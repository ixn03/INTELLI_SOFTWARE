from app.connectors.base import ConnectorMatch, PlatformConnector
from app.models.control_model import ControlProject


class UnsupportedConnector(PlatformConnector):
    def __init__(self, platform: str, display_name: str, extensions: tuple[str, ...]) -> None:
        self.platform = platform
        self.display_name = display_name
        self._extensions = extensions

    def can_parse(self, filename: str, content: bytes) -> ConnectorMatch:
        if filename.lower().endswith(self._extensions):
            return ConnectorMatch(platform=self.platform, confidence=0.2)
        return ConnectorMatch(platform=self.platform, confidence=0.0)

    def parse(self, filename: str, content: bytes) -> ControlProject:
        raise NotImplementedError(
            f"{self.display_name} parsing is registered but not implemented yet."
        )
