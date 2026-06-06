#!/usr/bin/env python3
"""Stateless INTELLI OPC UA collector — polls Prosys (or any OPC UA server) and POSTs batches to the backend.

Environment:
  INTELLI_BACKEND_URL   e.g. http://localhost:8000
  OPCUA_ENDPOINT_URL    e.g. opc.tcp://host.docker.internal:53530/OPCUA/SimulationServer
  COLLECTOR_POLL_MS     default 1000
  COLLECTOR_SOURCE_SYSTEMS  default OPC_UA (comma-separated)
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("intelli.opcua_collector")


def _backend_url() -> str:
    return os.getenv("INTELLI_BACKEND_URL", "http://localhost:8000").rstrip("/")


def _opcua_endpoint() -> str:
    url = os.getenv("OPCUA_ENDPOINT_URL", "").strip()
    if not url:
        raise SystemExit("OPCUA_ENDPOINT_URL is required.")
    return url


def _source_systems() -> list[str]:
    raw = os.getenv("COLLECTOR_SOURCE_SYSTEMS", "OPC_UA")
    return [part.strip() for part in raw.split(",") if part.strip()]


def _poll_interval_s() -> float:
    return max(0.2, int(os.getenv("COLLECTOR_POLL_MS", "1000")) / 1000.0)


def load_registry_tags(backend: str, source_systems: list[str]) -> list[dict[str, Any]]:
    tags: list[dict[str, Any]] = []
    with httpx.Client(timeout=15.0) as client:
        for source in source_systems:
            response = client.get(
                f"{backend}/api/tags",
                params={"source_system": source, "is_enabled": True, "limit": 500},
            )
            response.raise_for_status()
            payload = response.json()
            tags.extend(payload.get("items", []))
    return tags


def _node_id_for_tag(tag: dict[str, Any]) -> str | None:
    return tag.get("node_id") or tag.get("source_address")


def _quality_from_status(status: Any) -> str:
    try:
        name = status.name if hasattr(status, "name") else str(status)
    except Exception:
        return "uncertain"
    upper = name.upper()
    if "GOOD" in upper:
        return "good"
    if "BAD" in upper:
        return "bad"
    if "STALE" in upper:
        return "stale"
    return "uncertain"


async def _read_nodes(
    endpoint: str, tag_specs: list[tuple[dict[str, Any], str]]
) -> list[dict[str, Any]]:
    from asyncua import Client, ua

    samples: list[dict[str, Any]] = []
    async with Client(url=endpoint) as client:
        for tag, node_id_str in tag_specs:
            try:
                node = client.get_node(ua.NodeId.from_string(node_id_str))
                dv = await node.read_data_value()
                value = dv.Value
                if hasattr(value, "Value"):
                    value = value.Value
                samples.append(
                    {
                        "tag_id": tag["id"],
                        "raw_tag_name": tag["raw_name"],
                        "value": value,
                        "timestamp": (
                            dv.SourceTimestamp or dv.ServerTimestamp or datetime.now(timezone.utc)
                        ).isoformat(),
                        "quality": _quality_from_status(dv.StatusCode),
                        "source": tag["source_system"],
                        "source_address": tag.get("source_address"),
                        "unit": tag.get("unit"),
                    }
                )
            except Exception as exc:
                log.warning(
                    "Read failed for %s -> %s: %s",
                    tag.get("canonical_name"),
                    node_id_str,
                    exc,
                )
    return samples


def post_batch(backend: str, samples: list[dict[str, Any]]) -> dict[str, Any]:
    if not samples:
        return {"accepted_count": 0, "rejected_count": 0, "accepted_ids": [], "rejections": []}
    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            f"{backend}/api/ingest/tag-samples/batch",
            json={"samples": samples},
        )
        response.raise_for_status()
        return response.json()


async def run_collector() -> None:
    backend = _backend_url()
    endpoint = _opcua_endpoint()
    source_systems = _source_systems()
    poll_s = _poll_interval_s()

    log.info("INTELLI backend: %s", backend)
    log.info("OPC UA endpoint: %s", endpoint)
    log.info("Source systems: %s", ", ".join(source_systems))

    tags = load_registry_tags(backend, source_systems)
    log.info("Registry tags loaded: %d", len(tags))

    tag_specs: list[tuple[dict[str, Any], str]] = []
    for tag in tags:
        node_id = _node_id_for_tag(tag)
        if not node_id:
            log.warning(
                "Skipping %s — no node_id/source_address in registry",
                tag.get("canonical_name"),
            )
            continue
        log.info(
            "Mapping %s -> %s (node_id=%s)",
            tag.get("raw_name"),
            tag.get("canonical_name"),
            node_id,
        )
        tag_specs.append((tag, node_id))

    if not tag_specs:
        raise SystemExit("No enabled OPC UA tags with node_id configured in registry.")

    log.info("Connecting to OPC UA server...")
    try:
        from asyncua import Client

        async with Client(url=endpoint) as client:
            await client.connect()
            log.info("OPC UA connection success.")
            await client.disconnect()
    except Exception as exc:
        log.error("OPC UA connection failed: %s", exc)
        raise SystemExit(1) from exc

    log.info("Starting poll loop (interval=%.2fs)", poll_s)
    while True:
        try:
            samples = await _read_nodes(endpoint, tag_specs)
            result = post_batch(backend, samples)
            log.info(
                "Batch result: accepted=%s rejected=%s",
                result.get("accepted_count"),
                result.get("rejected_count"),
            )
            if result.get("rejections"):
                for rej in result["rejections"]:
                    log.warning("Rejected: %s", rej)
        except Exception as exc:
            log.error("Collector cycle failed: %s", exc)
        await asyncio.sleep(poll_s)


def main() -> int:
    try:
        asyncio.run(run_collector())
    except KeyboardInterrupt:
        log.info("Collector stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
