#!/usr/bin/env python3
"""De-identify Rockwell L5X exports for safe corpus retention.

Rockwell ``.L5X`` exports embed third-party *provenance* in the XML header:
the originating company/owner, the engineer who created/edited the logic, the
vendor string on Add-On Instruction definitions, and the export timestamp.
Distinctive controller/program context names can also encode a real
site/customer/program code.

This tool removes that provenance while leaving the *logic* (rungs,
instructions, Structured Text, AOI definitions, UDTs, parameters, tasks,
data values, comments) byte-for-byte unchanged. It is deterministic: the same
input always produces the same output.

What it scrubs
--------------
* Author / owner / company / vendor attributes anywhere in the XML markup:
  ``Owner``, ``CreatedBy``, ``EditedBy``, ``Vendor`` -> ``"INTELLI"``.
* The ``ExportDate`` provenance stamp -> a fixed neutral date.
* (Optional, default ON) Distinctive proprietary controller/program *context*
  names that encode a real site/customer/program. Only names in an explicit
  rename map are touched, so generic tag/controller names and logic are never
  broken.

What it never touches
---------------------
* Anything inside ``<![CDATA[ ... ]]>`` (rung text, ST source, descriptions,
  comments) -- the actual control logic and documentation.
* Instruction structure, parameters, local tags, data values, UDTs, tasks.

Usage
-----
::

    cd backend
    python tools/deidentify_l5x.py path/to/file.L5X            # writes file.deid.L5X
    python tools/deidentify_l5x.py path/to/file.L5X --in-place # overwrite
    python tools/deidentify_l5x.py path/to/folder --in-place   # all *.L5X in folder
    python tools/deidentify_l5x.py file.L5X --no-rename         # provenance only

Importable API
--------------
``deidentify_text(text, name_map=..., rename=...) -> (new_text, changes)``
``deidentify_file(path, in_place=..., ...) -> DeidResult``
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Neutral replacement values for identifying provenance.
NEUTRAL_OWNER = "INTELLI"
NEUTRAL_AUTHOR = "INTELLI"
NEUTRAL_VENDOR = "INTELLI"
NEUTRAL_EXPORT_DATE = "Thu Jan 01 00:00:00 2026"

# XML attributes whose *values* are author/owner/company/export provenance.
# Each is replaced with a neutral constant wherever it appears as an attribute
# (outside CDATA). Order is fixed for deterministic output.
PROVENANCE_ATTRS: dict[str, str] = {
    "Owner": NEUTRAL_OWNER,
    "CreatedBy": NEUTRAL_AUTHOR,
    "EditedBy": NEUTRAL_AUTHOR,
    "Vendor": NEUTRAL_VENDOR,
    "ExportDate": NEUTRAL_EXPORT_DATE,
}

# Distinctive proprietary controller/program context names that encode a real
# site / customer / program. These are NOT logic-bearing (they are the
# originating project's context label on an export), so renaming them is safe.
#
# Conservative by design: ONLY names listed here are renamed. Generic names
# such as "AOi", "Compact_AOI_Test", "Mini_Ctrl" are intentionally left alone.
# Add entries here (or pass --name-map) as new proprietary names are found.
DEFAULT_NAME_MAP: dict[str, str] = {
    # "P_24315_VentraP552LDMToHousing" encodes a real automotive customer
    # program (Ventra P552); the AOI logic itself is a generic sequence timer.
    "P_24315_VentraP552LDMToHousing": "INTELLI_AOI_Context",
}

_CDATA_SPLIT_RE = re.compile(r"(<!\[CDATA\[.*?\]\]>)", re.DOTALL)


@dataclass
class DeidResult:
    """Outcome of de-identifying one file."""

    path: Path
    changed: bool
    output_path: Path | None
    changes: list[tuple[str, str, str]] = field(default_factory=list)


def _attr_pattern(attr: str) -> re.Pattern[str]:
    # Match Attr="value" where value contains no double quote. L5X escapes
    # embedded quotes as &quot;, so a [^"]* value capture is exact.
    return re.compile(rf'(\b{re.escape(attr)}=")([^"]*)(")')


def deidentify_text(
    text: str,
    name_map: dict[str, str] | None = None,
    rename: bool = True,
) -> tuple[str, list[tuple[str, str, str]]]:
    """Return ``(deidentified_text, changes)``.

    ``changes`` is a list of ``(field, old_value, new_value)`` tuples describing
    every substitution made, in document order. CDATA content is never altered.
    """

    name_map = DEFAULT_NAME_MAP if name_map is None else name_map
    changes: list[tuple[str, str, str]] = []

    # Split into alternating non-CDATA / CDATA segments; only scrub the
    # non-CDATA (markup) segments so logic and comments stay byte-identical.
    segments = _CDATA_SPLIT_RE.split(text)
    for idx, segment in enumerate(segments):
        if segment.startswith("<![CDATA["):
            continue

        new_segment = segment

        for attr, neutral in PROVENANCE_ATTRS.items():
            pattern = _attr_pattern(attr)

            def _replace(match: re.Match[str], _attr=attr, _neutral=neutral) -> str:
                old_value = match.group(2)
                if old_value != _neutral:
                    changes.append((_attr, old_value, _neutral))
                return f"{match.group(1)}{_neutral}{match.group(3)}"

            new_segment = pattern.sub(_replace, new_segment)

        if rename:
            for old_name, new_name in name_map.items():
                if old_name == new_name:
                    continue
                quoted_old = f'"{old_name}"'
                if quoted_old in new_segment:
                    occurrences = new_segment.count(quoted_old)
                    new_segment = new_segment.replace(quoted_old, f'"{new_name}"')
                    for _ in range(occurrences):
                        changes.append(("Name", old_name, new_name))

        segments[idx] = new_segment

    return "".join(segments), changes


def deidentify_file(
    path: Path,
    in_place: bool = False,
    output_path: Path | None = None,
    name_map: dict[str, str] | None = None,
    rename: bool = True,
) -> DeidResult:
    """De-identify a single L5X file.

    Reads/writes raw bytes (decoding as UTF-8) so that everything except the
    scrubbed substrings is preserved byte-for-byte.
    """

    raw = path.read_bytes()
    original = raw.decode("utf-8")
    new_text, changes = deidentify_text(original, name_map=name_map, rename=rename)
    changed = new_text != original

    target: Path | None
    if in_place:
        target = path
    elif output_path is not None:
        target = output_path
    else:
        target = path.with_suffix(".deid" + path.suffix)

    if changed or not in_place:
        target.write_bytes(new_text.encode("utf-8"))
        written = target
    else:
        written = None

    return DeidResult(path=path, changed=changed, output_path=written, changes=changes)


def _iter_l5x(target: Path) -> list[Path]:
    if target.is_dir():
        return sorted(p for p in target.iterdir() if p.is_file() and p.suffix.lower() == ".l5x")
    return [target]


def _parse_name_map(spec: str | None) -> dict[str, str] | None:
    if not spec:
        return None
    mapping = dict(DEFAULT_NAME_MAP)
    for pair in spec.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            raise ValueError(f"Invalid --name-map entry (expected OLD=NEW): {pair!r}")
        old, new = pair.split("=", 1)
        mapping[old.strip()] = new.strip()
    return mapping


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="De-identify Rockwell L5X exports.")
    ap.add_argument("target", type=Path, help="L5X file or a folder of *.L5X files")
    ap.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite the source file(s) instead of writing *.deid.L5X copies.",
    )
    ap.add_argument(
        "--no-rename",
        action="store_true",
        help="Only scrub provenance attributes; do not rename proprietary context names.",
    )
    ap.add_argument(
        "--name-map",
        default=None,
        help='Extra context-name renames, e.g. "OldName=NewName,Other=Neutral". '
        "Merged with the built-in default map.",
    )
    args = ap.parse_args(argv)

    target: Path = args.target
    if not target.exists():
        print(f"Path not found: {target}", file=sys.stderr)
        return 2

    try:
        name_map = _parse_name_map(args.name_map)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    files = _iter_l5x(target)
    if not files:
        print(f"No .L5X files found at {target}", file=sys.stderr)
        return 1

    for path in files:
        result = deidentify_file(
            path,
            in_place=args.in_place,
            name_map=name_map,
            rename=not args.no_rename,
        )
        if result.changes:
            print(f"{path.name}: {len(result.changes)} substitution(s)")
            # Collapse duplicate (field, old, new) tuples for a tidy report.
            seen: dict[tuple[str, str, str], int] = {}
            for change in result.changes:
                seen[change] = seen.get(change, 0) + 1
            for (field_name, old, new), count in seen.items():
                suffix = f" x{count}" if count > 1 else ""
                print(f'    {field_name}: "{old}" -> "{new}"{suffix}')
        else:
            print(f"{path.name}: no identifiers found")
        if result.output_path is not None:
            print(f"    written: {result.output_path.name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
