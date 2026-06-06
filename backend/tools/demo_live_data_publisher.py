#!/usr/bin/env python3
"""Publish changing demo tag values through the live ingestion API.

This is a Prosys-free validation path for Step 3B. It exercises the same
backend ingestion endpoint and InfluxDB writer that the OPC UA collector uses.

Environment:
  INTELLI_BACKEND_URL      default http://localhost:8000
  DEMO_PUBLISH_INTERVAL_MS default 1000
  DEMO_PUBLISH_COUNT       optional finite number of batches
  DEMO_SOURCE_OVERRIDE     optional sample source override, e.g. SIMULATOR
"""

from __future__ import annotations

import math
import os
import time
from datetime import datetime, timezone
from typing import Any

import httpx


DEMO_TAG_NAMES = {
    "P101_RunCmd",
    "P101_RunningFB",
    "P101_Fault",
    "XV101_OpenCmd",
    "XV101_OpenFB",
    "Tank101_Level",
}


def _backend_url() -> str:
    return os.getenv("INTELLI_BACKEND_URL", "http://localhost:8000").rstrip("/")


def _interval_s() -> float:
    raw = os.getenv("DEMO_PUBLISH_INTERVAL_MS", "1000")
    return max(0.2, int(raw) / 1000.0)


def _publish_count() -> int | None:
    raw = os.getenv("DEMO_PUBLISH_COUNT", "").strip()
    if not raw:
        return None
    return max(1, int(raw))


def _source_for_tag(tag: dict[str, Any]) -> str:
    return os.getenv("DEMO_SOURCE_OVERRIDE", "").strip() or tag["source_system"]


def _load_demo_tags(backend: str) -> list[dict[str, Any]]:
    with httpx.Client(timeout=15.0) as client:
        response = client.get(
            f"{backend}/api/tags",
            params={"is_enabled": True, "limit": 500},
        )
        response.raise_for_status()
        tags = response.json().get("items", [])

    by_name = {tag["canonical_name"]: tag for tag in tags}
    missing = sorted(DEMO_TAG_NAMES - set(by_name))
    if missing:
        raise SystemExit(f"Missing demo tags from registry: {', '.join(missing)}")
    return [by_name[name] for name in sorted(DEMO_TAG_NAMES)]


def _demo_value(canonical_name: str, tick: int) -> bool | float:
    phase = tick % 12
    if canonical_name == "P101_RunCmd":
        return phase < 6
    if canonical_name == "P101_RunningFB":
        return ((tick + 1) % 12) < 6
    if canonical_name == "P101_Fault":
        return tick % 30 == 0
    if canonical_name == "XV101_OpenCmd":
        return (tick // 4) % 2 == 0
    if canonical_name == "XV101_OpenFB":
        return ((tick + 2) // 4) % 2 == 0
    if canonical_name == "Tank101_Level":
        return round(50.0 + 35.0 * math.sin(tick / 5.0), 3)
    raise KeyError(canonical_name)


def _build_batch(tags: list[dict[str, Any]], tick: int) -> dict[str, Any]:
    timestamp = datetime.now(timezone.utc).isoformat()
    return {
        "samples": [
            {
                "tag_id": tag["id"],
                "raw_tag_name": tag["raw_name"],
                "value": _demo_value(tag["canonical_name"], tick),
                "timestamp": timestamp,
                "quality": "good",
                "source": _source_for_tag(tag),
                "source_address": tag.get("source_address"),
                "unit": tag.get("unit"),
            }
            for tag in tags
        ]
    }


def _post_batch(backend: str, payload: dict[str, Any]) -> dict[str, Any]:
    with httpx.Client(timeout=30.0) as client:
        response = client.post(f"{backend}/api/ingest/tag-samples/batch", json=payload)
        response.raise_for_status()
        return response.json()


def main() -> int:
    backend = _backend_url()
    interval_s = _interval_s()
    publish_count = _publish_count()

    print(f"INTELLI backend: {backend}")
    print(f"Publish interval: {interval_s:.2f}s")
    print(f"Publish count: {publish_count if publish_count is not None else 'infinite'}")

    tags = _load_demo_tags(backend)
    print("Demo tag mapping:")
    for tag in tags:
        print(
            f"  {tag['canonical_name']} id={tag['id']} "
            f"source={_source_for_tag(tag)} address={tag.get('source_address')}"
        )

    tick = 0
    try:
        while publish_count is None or tick < publish_count:
            payload = _build_batch(tags, tick)
            result = _post_batch(backend, payload)
            print(
                "Batch {tick}: accepted={accepted} rejected={rejected}".format(
                    tick=tick,
                    accepted=result.get("accepted_count"),
                    rejected=result.get("rejected_count"),
                )
            )
            for rejection in result.get("rejections", []):
                print(f"  Rejected: {rejection}")
            tick += 1
            if publish_count is None or tick < publish_count:
                time.sleep(interval_s)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
