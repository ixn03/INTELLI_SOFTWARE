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
    "commands": ("Commands", "commands", _command_names),
    "devices": ("Devices", "commands[].devices", lambda e: _command_values(e, "devices")),
    "steps": ("Sequence steps", "commands[].steps", lambda e: _command_values(e, "steps")),
    "tags": ("Tags", "tags", _tags),
    "routines": ("Routines", "routines", lambda e: _as_string_list(e.get("routines"))),
    "setpoints": ("Setpoints", "setpoints", _setpoints),
    "permissives": ("Permissives", "permissives", lambda e: _as_string_list(e.get("permissives"))),
    "interlocks": ("Interlocks", "interlocks", lambda e: _as_string_list(e.get("interlocks"))),
    "conditions": ("Conditions", "conditions", lambda e: _as_string_list(e.get("conditions"))),
    "alarms": ("Alarms", "alarms", lambda e: _as_string_list(e.get("alarms"))),
    "operator_prompts": ("Operator prompts", "operator_prompts", lambda e: _as_string_list(e.get("operator_prompts"))),
    "causes": ("Causes", "causes", lambda e: _first_present(e, "causes")),
    "effects": ("Effects", "effects", lambda e: _as_string_list(e.get("effects"))),
    "actions": ("Actions", "actions", lambda e: _as_string_list(e.get("actions"))),
    "priorities": ("Alarm priorities", "priorities", lambda e: _first_present(e, "priorities", "priority")),
    "directions": ("IO directions", "directions", lambda e: _first_present(e, "directions", "direction")),
    "data_types": ("Data types", "data_types", lambda e: _first_present(e, "data_types", "data_type")),
    "descriptions": ("Descriptions", "descriptions", lambda e: _first_present(e, "descriptions", "description")),
    "operator_responses": ("Operator responses", "operator_responses", lambda e: _as_string_list(e.get("operator_responses"))),
    "consequences": ("Consequences", "consequences", lambda e: _as_string_list(e.get("consequences"))),
}

# Which fact keys are relevant per record type (drives the facts package).
RECORD_TYPE_FACT_KEYS: dict[str, list[str]] = {
    "control_narrative": [
        "commands", "devices", "steps", "routines", "tags",
        "permissives", "interlocks", "conditions", "setpoints",
        "alarms", "operator_prompts",
    ],
    "io_list": ["tags", "descriptions", "directions", "data_types"],
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

    keys = RECORD_TYPE_FACT_KEYS.get(record_type, list(_FACT_DEFS.keys()))
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


def fact_keys_for_section(section: str) -> list[str]:
    """Map a template section heading to the fact keys that feed it."""

    normalized = section.lower()
    if "equipment controlled" in normalized or "related module" in normalized:
        return ["tags"]
    if "command" in normalized:
        return ["commands", "devices"]
    if "sequence" in normalized:
        return ["routines", "steps", "commands"]
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
    return []


def facts_for_section(
    section: str,
    record_type: str,
    facts: list[GenerationSourceFact],
) -> list[GenerationSourceFact]:
    keys = fact_keys_for_section(section)
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
