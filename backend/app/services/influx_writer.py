"""InfluxDB 3 Core writer for tag_samples measurement."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

_ESCAPE_RE = re.compile(r"([\\ ,=])")


def _escape_tag_value(value: str) -> str:
    return _ESCAPE_RE.sub(lambda m: "\\" + m.group(1), value)


def _ns_timestamp(ts: datetime) -> int:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return int(ts.timestamp() * 1_000_000_000)


@dataclass(frozen=True)
class InfluxSampleRecord:
    tag_id: str
    raw_tag_name: str
    source: str
    quality: str
    plant_id: str
    equipment_id: str | None
    value: bool | int | float | str
    timestamp: datetime


class InfluxWriter:
    """Write accepted tag samples to InfluxDB 3 via /api/v3/write_lp."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        database: str | None = None,
        token: str | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        self.base_url = (base_url or os.getenv("INFLUXDB_URL", "http://localhost:8181")).rstrip(
            "/"
        )
        self.database = database or os.getenv("INFLUXDB_BUCKET") or os.getenv(
            "INFLUXDB_DB", "intelli_ts"
        )
        self.token = token if token is not None else os.getenv("INFLUXDB_TOKEN")
        self.timeout_s = timeout_s

    def write_samples(self, records: list[InfluxSampleRecord]) -> None:
        if not records:
            return
        lines = [self._to_line_protocol(rec) for rec in records]
        body = "\n".join(lines)
        url = (
            f"{self.base_url}/api/v3/write_lp"
            f"?db={quote(self.database)}&precision=nanosecond"
        )
        headers = {"Content-Type": "text/plain; charset=utf-8"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        with httpx.Client(timeout=self.timeout_s) as client:
            response = client.post(url, content=body, headers=headers)
            response.raise_for_status()

    def _to_line_protocol(self, rec: InfluxSampleRecord) -> str:
        equipment = rec.equipment_id or "none"
        tags = ",".join(
            [
                "tag_samples",
                f"tag_id={_escape_tag_value(rec.tag_id)}",
                f"raw_tag_name={_escape_tag_value(rec.raw_tag_name)}",
                f"source={_escape_tag_value(rec.source)}",
                f"quality={_escape_tag_value(rec.quality)}",
                f"plant_id={_escape_tag_value(rec.plant_id)}",
                f"equipment_id={_escape_tag_value(equipment)}",
            ]
        )
        field = self._value_field(rec.value)
        return f"{tags} {field} {_ns_timestamp(rec.timestamp)}"

    @staticmethod
    def _value_field(value: bool | int | float | str) -> str:
        if isinstance(value, bool):
            return f"value_bool={str(value).lower()}"
        if isinstance(value, int):
            return f"value_int={value}i"
        if isinstance(value, float):
            return f"value_float={value}"
        escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
        return f'value_string="{escaped}"'


_default_writer: InfluxWriter | None = None


def get_influx_writer() -> InfluxWriter:
    global _default_writer
    if _default_writer is None:
        _default_writer = InfluxWriter()
    return _default_writer


def set_influx_writer(writer: InfluxWriter | None) -> None:
    global _default_writer
    _default_writer = writer
