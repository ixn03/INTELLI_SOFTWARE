"""InfluxDB 3 Core reader for tag_samples."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

import uuid as _uuid

from app.schemas.tag_sample import SampleQuality, TagSampleRead


class InfluxReaderError(Exception):
    pass


class InfluxReader:
    """Query tag_samples from InfluxDB 3 via /api/v3/query_sql."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        database: str | None = None,
        token: str | None = None,
        timeout_s: float = 15.0,
    ) -> None:
        self.base_url = (base_url or os.getenv("INFLUXDB_URL", "http://localhost:8181")).rstrip(
            "/"
        )
        self.database = database or os.getenv("INFLUXDB_BUCKET") or os.getenv(
            "INFLUXDB_DB", "intelli_ts"
        )
        self.token = token if token is not None else os.getenv("INFLUXDB_TOKEN")
        self.timeout_s = timeout_s

    def get_latest_tag_value(self, tag_id: str) -> TagSampleRead | None:
        query = f"""
            SELECT *
            FROM tag_samples
            WHERE tag_id = '{self._escape_sql(tag_id)}'
            ORDER BY time DESC
            LIMIT 1
        """
        rows = self._query(query)
        if not rows:
            return None
        return self._row_to_sample(rows[0])

    def get_tag_history(
        self,
        tag_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
    ) -> list[TagSampleRead]:
        clauses = [f"tag_id = '{self._escape_sql(tag_id)}'"]
        if start is not None:
            clauses.append(f"time >= '{self._format_ts(start)}'")
        if end is not None:
            clauses.append(f"time <= '{self._format_ts(end)}'")
        where = " AND ".join(clauses)
        query = f"""
            SELECT *
            FROM tag_samples
            WHERE {where}
            ORDER BY time DESC
            LIMIT {int(limit)}
        """
        return [self._row_to_sample(row) for row in self._query(query)]

    def _query(self, sql: str) -> list[dict[str, Any]]:
        url = f"{self.base_url}/api/v3/query_sql"
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        payload = {"db": self.database, "q": sql.strip(), "format": "json"}
        with httpx.Client(timeout=self.timeout_s) as client:
            response = client.post(url, json=payload, headers=headers)
            if response.status_code >= 400:
                raise InfluxReaderError(
                    f"Influx query failed ({response.status_code}): {response.text}"
                )
            data = response.json()
        return self._normalize_rows(data)

    @staticmethod
    def _normalize_rows(data: Any) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            if "data" in data and isinstance(data["data"], list):
                return data["data"]
            if "results" in data and isinstance(data["results"], list):
                return data["results"]
        # jsonl fallback: one JSON object per line
        if isinstance(data, str):
            rows = []
            for line in data.splitlines():
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
            return rows
        return []

    def _row_to_sample(self, row: dict[str, Any]) -> TagSampleRead:
        value = self._extract_value(row)
        ts_raw = row.get("time") or row.get("timestamp")
        timestamp = self._parse_time(ts_raw)
        from app.db.models.tag_registry import SourceSystem

        source_raw = str(row.get("source", "OPC_UA"))
        quality_raw = str(row.get("quality", "good")).lower()
        plant_raw = row.get("plant_id")
        equip_raw = row.get("equipment_id")
        plant_id = _uuid.UUID(str(plant_raw)) if plant_raw else None
        equipment_id = None
        if equip_raw not in (None, "none", ""):
            equipment_id = _uuid.UUID(str(equip_raw))
        return TagSampleRead(
            tag_id=_uuid.UUID(str(row.get("tag_id"))),
            raw_tag_name=str(row.get("raw_tag_name", "")),
            value=value,
            timestamp=timestamp,
            quality=SampleQuality(quality_raw),
            source=SourceSystem(source_raw),
            source_address=None,
            unit=None,
            plant_id=plant_id,
            equipment_id=equipment_id,
        )

    @staticmethod
    def _extract_value(row: dict[str, Any]) -> bool | int | float | str:
        if row.get("value_bool") is not None:
            raw = row["value_bool"]
            if isinstance(raw, str):
                return raw.lower() == "true"
            return bool(raw)
        if row.get("value_int") is not None:
            return int(row["value_int"])
        if row.get("value_float") is not None:
            return float(row["value_float"])
        if row.get("value_string") is not None:
            return str(row["value_string"])
        return ""

    @staticmethod
    def _parse_time(raw: Any) -> datetime:
        if isinstance(raw, datetime):
            return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
        if raw is None:
            return datetime.now(timezone.utc)
        text = str(raw).replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return datetime.now(timezone.utc)

    @staticmethod
    def _format_ts(ts: datetime) -> str:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.isoformat()

    @staticmethod
    def _escape_sql(value: str) -> str:
        return value.replace("'", "''")


_default_reader: InfluxReader | None = None


def get_influx_reader() -> InfluxReader:
    global _default_reader
    if _default_reader is None:
        _default_reader = InfluxReader()
    return _default_reader


def set_influx_reader(reader: InfluxReader | None) -> None:
    global _default_reader
    _default_reader = reader
