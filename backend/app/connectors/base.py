from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

from app.models.control_model import ControlProject


@dataclass(frozen=True)
class ConnectorMatch:
    """Result of ``can_parse`` — how strongly this connector claims the file."""

    platform: str
    confidence: float


class PlatformConnector(ABC):
    """Vendor-neutral ingest entry point.

    Implementations must not invent logic for formats they do not truly parse.
    """

    platform: ClassVar[str]
    display_name: ClassVar[str]
    supported_extensions: ClassVar[tuple[str, ...]] = ()
    parser_version: ClassVar[str] = "0"

    @abstractmethod
    def can_parse(self, filename: str, content: bytes) -> ConnectorMatch:
        raise NotImplementedError

    @abstractmethod
    def parse(self, filename: str, content: bytes) -> ControlProject:
        raise NotImplementedError
