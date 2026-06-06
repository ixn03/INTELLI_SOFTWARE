"""OPC UA client configuration helpers for INTELLI tools."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class OpcUaClientConfig:
    endpoint_url: str
    security_policy: str
    security_mode: str
    auth_mode: str


def load_opcua_client_config() -> OpcUaClientConfig:
    endpoint = os.getenv("OPCUA_ENDPOINT_URL", "").strip()
    if not endpoint:
        raise SystemExit("OPCUA_ENDPOINT_URL is required.")
    return OpcUaClientConfig(
        endpoint_url=endpoint,
        security_policy=os.getenv("OPCUA_SECURITY_POLICY", "None").strip() or "None",
        security_mode=os.getenv("OPCUA_SECURITY_MODE", "None").strip() or "None",
        auth_mode=os.getenv("OPCUA_AUTH_MODE", "Anonymous").strip() or "Anonymous",
    )


def _normalized(value: str) -> str:
    return value.strip().lower().replace("_", "").replace("-", "")


def configure_opcua_client(client: Any, config: OpcUaClientConfig) -> None:
    """Apply the currently supported MVP OPC UA settings to an asyncua client."""

    if _normalized(config.security_policy) not in {"none", "securitypolicynone"}:
        raise SystemExit(
            "Only OPCUA_SECURITY_POLICY=None is supported by the MVP collector."
        )
    if _normalized(config.security_mode) not in {"none", "messagesecuritymodenone"}:
        raise SystemExit("Only OPCUA_SECURITY_MODE=None is supported by the MVP collector.")
    if _normalized(config.auth_mode) not in {"anonymous", "anon"}:
        raise SystemExit("Only OPCUA_AUTH_MODE=Anonymous is supported by the MVP collector.")

    # asyncua defaults to SecurityPolicy=None, MessageSecurityMode=None, and
    # anonymous user identity when no certificate/user credentials are loaded.


def log_opcua_client_config(
    config: OpcUaClientConfig, logger: logging.Logger | None = None
) -> None:
    lines = [
        ("Endpoint", config.endpoint_url),
        ("SecurityPolicy", config.security_policy),
        ("SecurityMode", config.security_mode),
        ("Authentication", config.auth_mode),
    ]
    if logger is None:
        for label, value in lines:
            print(f"{label}: {value}")
        return
    for label, value in lines:
        logger.info("OPC UA %s: %s", label, value)


def _enum_name(value: Any) -> str:
    name = getattr(value, "name", None)
    if name:
        return str(name).rstrip("_")
    return str(value)


def format_endpoint_report(endpoints: Iterable[Any]) -> list[str]:
    lines: list[str] = []
    for endpoint in endpoints:
        tokens: list[str] = []
        for token in getattr(endpoint, "UserIdentityTokens", []) or []:
            token_type = getattr(token, "TokenType", "?")
            token_policy = getattr(token, "SecurityPolicyUri", "") or "None"
            tokens.append(f"{_enum_name(token_type)}:{token_policy}")
        lines.append(
            "EndpointUrl={url}  SecurityMode={mode}  SecurityPolicy={policy}  "
            "UserTokens={tokens}".format(
                url=getattr(endpoint, "EndpointUrl", "?"),
                mode=_enum_name(getattr(endpoint, "SecurityMode", "?")),
                policy=getattr(endpoint, "SecurityPolicyUri", "?"),
                tokens=", ".join(tokens) if tokens else "(none)",
            )
        )
    return lines
