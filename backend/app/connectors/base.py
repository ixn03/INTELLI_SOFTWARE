from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.models.control_model import ControlProject


@dataclass(frozen=True)
class ConnectorMatch:
    platform: str
    confidence: float


class PlatformConnector(ABC):
    platform: str
    display_name: str

    @abstractmethod
    def can_parse(self, filename: str, content: bytes) -> ConnectorMatch:
        raise NotImplementedError

    @abstractmethod
    def parse(self, filename: str, content: bytes) -> ControlProject:
        raise NotImplementedError
