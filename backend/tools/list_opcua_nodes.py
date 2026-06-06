#!/usr/bin/env python3
"""Browse OPC UA nodes under Simulation/PumpSystem (or report what is available).

Environment:
  OPCUA_ENDPOINT_URL  required, e.g. opc.tcp://localhost:53530/OPCUA/SimulationServer

Prints DisplayName, BrowseName, NodeId, DataType, and AccessLevel for each variable found.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from asyncua import Client, ua


def _endpoint() -> str:
    url = os.getenv("OPCUA_ENDPOINT_URL", "").strip()
    if not url:
        raise SystemExit("OPCUA_ENDPOINT_URL is required.")
    return url


async def _browse_name(client: Client, node: Any) -> str:
    try:
        bn = await client.get_node(node).read_browse_name()
        return str(bn.Name)
    except Exception:
        try:
            bn = node.BrowseName
            if hasattr(bn, "Name"):
                return str(bn.Name)
            return str(bn)
        except Exception:
            return "?"


def _node_id_str(node: Any) -> str:
    try:
        if hasattr(node, "nodeid"):
            return node.nodeid.to_string()
        return node.NodeId.to_string()
    except Exception:
        return "?"


async def _display_name(client: Client, node: Any) -> str:
    try:
        dn = await client.get_node(node).read_display_name()
        return str(dn.Text)
    except Exception:
        return "?"


async def _data_type(client: Client, node: Any) -> str:
    try:
        child = await client.get_node(node).read_data_type_as_variant_type()
        return str(child)
    except Exception:
        try:
            dt_node = await client.get_node(node).read_data_type()
            return dt_node.to_string()
        except Exception:
            return "?"


async def _access_level(client: Client, node: Any) -> str:
    try:
        level = await client.get_node(node).read_attribute(ua.AttributeIds.AccessLevel)
        return str(level.Value.Value)
    except Exception:
        return "?"


async def _print_variables(client: Client, folder_node: Any, label: str) -> int:
    print(f"\n--- {label} ---")
    count = 0
    children = await client.get_node(folder_node).get_children()
    for child in children:
        try:
            node_class = await client.get_node(child).read_node_class()
        except Exception:
            continue
        if node_class != ua.NodeClass.Variable:
            continue
        count += 1
        display = await _display_name(client, child)
        browse = await _browse_name(client, child)
        node_id = _node_id_str(child)
        data_type = await _data_type(client, child)
        access = await _access_level(client, child)
        print(
            f"DisplayName={display!r}  BrowseName={browse!r}  "
            f"NodeId={node_id}  DataType={data_type}  AccessLevel={access}"
        )
    if count == 0:
        print("(no Variable nodes found)")
    return count


async def _find_pump_system(client: Client) -> tuple[Any | None, str]:
    """Try common paths under Objects to locate Simulation/PumpSystem."""
    objects = client.nodes.objects
    candidates: list[tuple[Any, str]] = []

    async def walk(parent: Any, path: str, depth: int) -> None:
        if depth > 4:
            return
        try:
            children = await client.get_node(parent).get_children()
        except Exception:
            return
        for child in children:
            name = await _browse_name(client, child)
            child_path = f"{path}/{name}"
            lower = name.lower()
            if lower in ("pumpsystem", "pump_system"):
                candidates.append((child, child_path))
            if lower in ("simulation", "sim"):
                candidates.append((child, child_path))
            await walk(child, child_path, depth + 1)

    await walk(objects, "Objects", 0)

    for node, path in candidates:
        if path.lower().endswith("simulation/pumpsystem") or "pumpsystem" in path.lower():
            return node, path

    for node, path in candidates:
        if "pumpsystem" in path.lower():
            return node, path

    direct_paths = [
        "Objects/Simulation/PumpSystem",
        "Objects/Simulation",
        "Objects/PumpSystem",
    ]
    for rel_path in direct_paths:
        parts = rel_path.split("/")[1:]
        try:
            current = objects
            built: list[str] = ["Objects"]
            for part in parts:
                current = await current.get_child(part)
                built.append(part)
            return current, "/".join(built)
        except Exception:
            continue

    return None, ""


async def _print_top_level(client: Client) -> None:
    print("\n--- Top-level under Objects ---")
    children = await client.nodes.objects.get_children()
    for child in children:
        name = await _browse_name(client, child)
        node_id = _node_id_str(child)
        display = await _display_name(client, child)
        print(f"  {display!r} ({name})  NodeId={node_id}")


async def browse() -> int:
    endpoint = _endpoint()
    print(f"Connecting to {endpoint}")

    async with Client(url=endpoint) as client:
        folder, path = await _find_pump_system(client)

        if folder is None:
            print(
                "\nPumpSystem folder not found under Objects. "
                "Create variables under Simulation/PumpSystem in Prosys, then re-run."
            )
            await _print_top_level(client)
            return 1

        print(f"Found folder: {path}")
        count = await _print_variables(client, folder, f"Variables in {path}")

        if count == 0:
            print(
                "\nFolder exists but contains no variables. "
                "Add BOOL/REAL variables in Prosys (P101_RunCmd, etc.) and copy NodeIds from Browse."
            )

    return 0


def main() -> int:
    try:
        return asyncio.run(browse())
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
