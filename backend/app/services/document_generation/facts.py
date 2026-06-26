"""Extract structured source facts (with provenance) from trusted inputs.

Only ``LogicSnapshot.parsed_extract`` (plus module/snapshot metadata) is treated
as a fact source here. The raw vendor export is never read. Each fact carries a
``source_field`` so generated prose stays traceable.
"""

from __future__ import annotations

from typing import Any, Callable

from app.services.document_generation.models import GenerationSourceFact


def _as_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, dict):
        items: list[str] = []
        for key, item in value.items():
            if isinstance(item, dict):
                label = item.get("name") or item.get("tag") or item.get("description") or key
                items.append(str(label))
            else:
                items.append(str(key if item in (None, True, "") else item))
        return [item for item in items if item]
    if isinstance(value, list):
        items = []
        for item in value:
            if isinstance(item, dict):
                label = item.get("name") or item.get("tag") or item.get("description") or item.get("id")
                if label:
                    items.append(str(label))
            elif item is not None:
                items.append(str(item))
        return [item for item in items if item]
    return [str(value)]


def _commands(parsed_extract: dict[str, Any]) -> dict[str, Any]:
    commands = parsed_extract.get("commands")
    return commands if isinstance(commands, dict) else {}


def _command_values(parsed_extract: dict[str, Any], key: str) -> list[str]:
    values: list[str] = []
    for command in _commands(parsed_extract).values():
        if isinstance(command, dict):
            values.extend(_as_string_list(command.get(key)))
    return sorted(set(values))


def _command_names(parsed_extract: dict[str, Any]) -> list[str]:
    return sorted(_commands(parsed_extract).keys())


def _tags(parsed_extract: dict[str, Any]) -> list[str]:
    tags = _as_string_list(parsed_extract.get("tags"))
    tags.extend(_as_string_list(parsed_extract.get("tag")))
    tags.extend(_command_values(parsed_extract, "tags"))
    tags.extend(_command_values(parsed_extract, "devices"))
    return sorted(set(tags))


def _io_tags(parsed_extract: dict[str, Any]) -> list[dict[str, Any]]:
    items = parsed_extract.get("io_tags") or parsed_extract.get("tags")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and item.get("tag")]


def _io_rows(parsed_extract: dict[str, Any]) -> list[str]:
    existing = _as_string_list(parsed_extract.get("io_rows"))
    if existing:
        return existing
    rows: list[str] = []
    for item in _io_tags(parsed_extract):
        source = item.get("program") or item.get("source") or "unknown"
        rows.append(
            " | ".join(
                [
                    str(item.get("tag") or ""),
                    f"direction={item.get('direction') or 'unknown'}",
                    f"data_type={item.get('data_type') or 'unknown'}",
                    f"description={item.get('description') or 'Needs engineer input'}",
                    f"source={source}",
                ]
            )
        )
    return rows


def _io_field(parsed_extract: dict[str, Any], field: str, *fallback_keys: str) -> list[str]:
    existing = _first_present(parsed_extract, field, *fallback_keys)
    if existing:
        return existing
    values: list[str] = []
    for item in _io_tags(parsed_extract):
        value = item.get(field)
        if field == "source":
            value = item.get("program") or item.get("source")
        if value:
            values.append(f"{item['tag']}: {value}")
    return sorted(set(values))


def _source_values(parsed_extract: dict[str, Any]) -> list[str]:
    return _io_field(parsed_extract, "source", "sources")


def _format_access_entries(parsed_extract: dict[str, Any], key: str) -> list[str]:
    entries: list[str] = []
    for item in parsed_extract.get(key) or []:
        if not isinstance(item, dict):
            continue
        tag = item.get("tag")
        if not tag:
            continue
        location = " / ".join(
            str(piece)
            for piece in (item.get("program"), item.get("routine"))
            if piece
        )
        entries.append(f"{tag} ({location})" if location else str(tag))
    return sorted(set(entries))


def _setpoints(parsed_extract: dict[str, Any]) -> list[str]:
    values = _as_string_list(parsed_extract.get("setpoints"))
    values.extend(_command_values(parsed_extract, "setpoints"))
    return sorted(set(values))


def _first_present(parsed_extract: dict[str, Any], *keys: str) -> list[str]:
    for key in keys:
        values = _as_string_list(parsed_extract.get(key))
        if values:
            return values
    return []


# key -> (label, source_field suffix, extractor)
_FactDef = tuple[str, str, Callable[[dict[str, Any]], list[str]]]

_FACT_DEFS: dict[str, _FactDef] = {
    "programs": ("Programs", "programs", lambda e: _as_string_list(e.get("programs"))),
    "commands": ("Commands", "commands", _command_names),
    "devices": ("Devices", "commands[].devices", lambda e: _command_values(e, "devices")),
    "steps": ("Sequence steps", "commands[].steps", lambda e: _command_values(e, "steps")),
    "tags": ("Tags", "tags", _tags),
    "io_rows": ("IO rows", "io_tags", _io_rows),
    "routines": ("Routines", "routines", lambda e: _as_string_list(e.get("routines"))),
    "setpoints": ("Setpoints", "setpoints", _setpoints),
    "permissives": ("Permissives", "permissives", lambda e: _as_string_list(e.get("permissives"))),
    "interlocks": ("Interlocks", "interlocks", lambda e: _as_string_list(e.get("interlocks"))),
    "conditions": ("Conditions", "conditions", lambda e: _as_string_list(e.get("conditions"))),
    "alarms": ("Alarms", "alarms", lambda e: _as_string_list(e.get("alarms"))),
    "operator_prompts": ("Operator prompts", "operator_prompts", lambda e: _as_string_list(e.get("operator_prompts"))),
    "outputs": ("Outputs/actions", "outputs", lambda e: _as_string_list(e.get("outputs"))),
    "reads": ("Reads", "reads", lambda e: _format_access_entries(e, "reads")),
    "writes": ("Writes", "writes", lambda e: _format_access_entries(e, "writes")),
    "causes": ("Causes", "causes", lambda e: _first_present(e, "causes")),
    "effects": ("Effects", "effects", lambda e: _as_string_list(e.get("effects"))),
    "actions": ("Actions", "actions", lambda e: _as_string_list(e.get("actions"))),
    "priorities": ("Alarm priorities", "priorities", lambda e: _first_present(e, "priorities", "priority")),
    "directions": ("IO directions", "directions", lambda e: _io_field(e, "direction", "directions")),
    "data_types": ("Data types", "data_types", lambda e: _io_field(e, "data_type", "data_types")),
    "descriptions": ("Descriptions", "descriptions", lambda e: _io_field(e, "description", "descriptions")),
    "sources": ("Sources", "sources", _source_values),
    "operator_responses": ("Operator responses", "operator_responses", lambda e: _as_string_list(e.get("operator_responses"))),
    "consequences": ("Consequences", "consequences", lambda e: _as_string_list(e.get("consequences"))),
}

# Which fact keys are relevant per record type (drives the facts package).
RECORD_TYPE_FACT_KEYS: dict[str, list[str]] = {
    "control_narrative": [
        "programs", "commands", "devices", "steps", "routines", "tags",
        "reads", "writes", "outputs", "actions",
        "permissives", "interlocks", "conditions", "setpoints",
        "alarms", "operator_prompts",
    ],
    "io_list": ["io_rows", "tags", "descriptions", "directions", "data_types", "sources"],
    "cause_effect_matrix": ["causes", "effects", "conditions", "actions", "tags"],
    "alarm_rationalization": [
        "alarms", "priorities", "causes", "operator_responses",
        "consequences", "operator_prompts", "tags",
    ],
}


def build_source_facts(
    *,
    record_type: str,
    parsed_extract: dict[str, Any],
    source_snapshot_id: str,
) -> list[GenerationSourceFact]:
    """Build the ordered fact list for a record type (present and missing)."""

    record_type_key = str(getattr(record_type, "value", record_type))
    keys = RECORD_TYPE_FACT_KEYS.get(record_type_key, list(_FACT_DEFS.keys()))
    facts: list[GenerationSourceFact] = []
    for key in keys:
        definition = _FACT_DEFS.get(key)
        if definition is None:
            continue
        label, field_suffix, extractor = definition
        values = extractor(parsed_extract or {})
        facts.append(
            GenerationSourceFact(
                key=key,
                label=label,
                values=values,
                source_field=f"LogicSnapshot.parsed_extract.{field_suffix}",
                source_kind="parsed_extract",
                source_snapshot_id=source_snapshot_id,
            )
        )
    return facts


def fact_keys_for_section(section: str, record_type: str | None = None) -> list[str]:
    """Map a template section heading to the fact keys that feed it."""

    normalized = section.lower()
    record_type_key = str(getattr(record_type, "value", record_type))
    if record_type_key == "io_list":
        if normalized in {"tag", "tags"} or "tag" in normalized:
            return ["io_rows", "tags"]
        if "source" in normalized:
            return ["sources"]
        if "related module" in normalized:
            return ["sources", "tags"]
    if "equipment controlled" in normalized or "related module" in normalized:
        return ["tags", "devices", "outputs"]
    if "command" in normalized:
        return ["commands", "devices", "outputs", "actions"]
    if "sequence" in normalized:
        return ["routines", "steps", "commands", "reads", "writes", "outputs"]
    if "permissive" in normalized or "interlock" in normalized or "condition" in normalized:
        return ["permissives", "interlocks", "conditions"]
    if "setpoint" in normalized:
        return ["setpoints"]
    if "alarm" in normalized:
        return ["alarms", "operator_prompts"]
    if "data type" in normalized:
        return ["data_types"]
    if "direction" in normalized:
        return ["directions"]
    if "description" in normalized:
        return ["descriptions"]
    if "operator response" in normalized:
        return ["operator_responses"]
    if "consequence" in normalized:
        return ["consequences"]
    if "priority" in normalized:
        return ["priorities"]
    if "cause" in normalized:
        return ["causes", "alarms", "conditions"]
    if "effect" in normalized or "action" in normalized:
        return ["effects", "actions"]
    if normalized in {"tag", "related tag/module"} or "tag" in normalized:
        return ["tags"]
    if "purpose" in normalized:
        return ["programs", "routines"]
    return []


def facts_for_section(
    section: str,
    record_type: str,
    facts: list[GenerationSourceFact],
) -> list[GenerationSourceFact]:
    keys = fact_keys_for_section(section, record_type)
    if not keys:
        return []
    by_key = {fact.key: fact for fact in facts}
    selected: list[GenerationSourceFact] = []
    for key in keys:
        fact = by_key.get(key)
        if fact is not None and fact.present:
            selected.append(fact)
    return selected


def allowed_fact_tokens(facts: list[GenerationSourceFact]) -> set[str]:
    """Token set used by the anti-hallucination guard."""

    tokens: set[str] = set()
    for fact in facts:
        for value in fact.values:
            tokens.add(value)
            for piece in str(value).replace(",", " ").split():
                tokens.add(piece)
    return tokens


__all__ = [
    "build_source_facts",
    "fact_keys_for_section",
    "facts_for_section",
    "allowed_fact_tokens",
    "RECORD_TYPE_FACT_KEYS",
]
