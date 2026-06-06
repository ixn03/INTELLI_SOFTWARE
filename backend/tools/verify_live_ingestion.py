#!/usr/bin/env python3
"""Verify latest/history live ingestion results for the six demo tags."""

from __future__ import annotations

import os
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


def _load_demo_tags(client: httpx.Client, backend: str) -> list[dict[str, Any]]:
    response = client.get(f"{backend}/api/tags", params={"is_enabled": True, "limit": 500})
    response.raise_for_status()
    tags = response.json().get("items", [])
    by_name = {tag["canonical_name"]: tag for tag in tags}
    return [by_name[name] for name in sorted(DEMO_TAG_NAMES) if name in by_name]


def _status(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def main() -> int:
    backend = _backend_url()
    print(f"INTELLI backend: {backend}")

    failures = 0
    with httpx.Client(timeout=30.0) as client:
        tags = _load_demo_tags(client, backend)
        found = {tag["canonical_name"] for tag in tags}
        for missing in sorted(DEMO_TAG_NAMES - found):
            failures += 1
            print(f"FAIL {missing}: tag missing from /api/tags")

        for tag in tags:
            name = tag["canonical_name"]
            latest_ok = False
            history_ok = False
            latest_detail = "no sample"
            history_detail = "0 samples"

            latest = client.get(f"{backend}/api/tags/{tag['id']}/latest")
            if latest.status_code == 200:
                latest_body = latest.json()
                sample = latest_body.get("sample")
                latest_ok = sample is not None and sample.get("value") is not None
                if sample is not None:
                    latest_detail = (
                        f"value={sample.get('value')!r} "
                        f"quality={sample.get('quality')} source={sample.get('source')}"
                    )
            else:
                latest_detail = f"HTTP {latest.status_code}: {latest.text}"

            history = client.get(
                f"{backend}/api/tags/{tag['id']}/history",
                params={"limit": 10},
            )
            if history.status_code == 200:
                history_body = history.json()
                samples = history_body.get("samples", [])
                history_ok = len(samples) >= 2
                history_detail = f"{len(samples)} samples"
            else:
                history_detail = f"HTTP {history.status_code}: {history.text}"

            if not latest_ok:
                failures += 1
            if not history_ok:
                failures += 1
            print(f"{_status(latest_ok)} {name} latest: {latest_detail}")
            print(f"{_status(history_ok)} {name} history: {history_detail}")

    if failures:
        print(f"\nLive ingestion verification failed: {failures} check(s) failed.")
        return 1
    print("\nLive ingestion verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
