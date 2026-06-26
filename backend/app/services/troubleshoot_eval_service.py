"""Private-safe troubleshooting workspace evaluation harness.

Runs a declarative case suite against the signal troubleshooting workspace
(either the service layer or the ``/api/troubleshoot/question`` route) and
emits aggregate accuracy metrics. Case fixtures may reference real tag names
internally, but public reports redact them to opaque signal keys.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from app.connectors.rockwell_l5x import RockwellL5XConnector
from app.models.reasoning import (
    ConfidenceLevel,
    ControlObject,
    ControlObjectType,
    Relationship,
    RelationshipType,
    WriteBehaviorType,
)
from app.services.normalization_service import normalize_l5x_project
from app.services.troubleshooting_workspace_service import (
    SignalTroubleshootingWorkspace,
    build_signal_workspace,
)

EvalMode = Literal["direct", "route"]

_FIXTURES_L5X = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "l5x"


@dataclass
class CheckResult:
    name: str
    passed: bool
    expected: Any
    actual: Any


@dataclass
class CaseResult:
    case_id: str
    fixture_id: str
    passed: bool
    checks: list[CheckResult]
    redacted_question: str


CHECK_TO_FAILURE_CATEGORY: dict[str, str] = {
    "target_resolution": "target_resolution",
    "target_unresolved": "target_resolution",
    "intent": "intent_detection",
    "writer_count": "writer_count",
    "no_writers": "writer_count",
    "upstream_recall": "upstream_recall",
    "downstream_count": "downstream_recall",
    "downstream_recall": "downstream_recall",
    "unknown_direction_count": "unknown_direction_handling",
    "missing_ladder_provenance": "missing_ladder_provenance",
    "missing_fbd_provenance": "missing_fbd_provenance",
    "missing_aoi_provenance": "missing_aoi_provenance",
    "explanation_quality": "vague_or_incomplete_explanation",
    "confidence": "vague_or_incomplete_explanation",
}


@dataclass
class AggregateReport:
    version: int
    mode: EvalMode
    total_cases: int
    passed_cases: int
    failed_cases: int
    pass_rate: float
    metrics: dict[str, float]
    by_fixture: dict[str, dict[str, float]]
    by_intent: dict[str, dict[str, float]]
    failure_categories: dict[str, int]
    recommended_next_fixes: list[str]
    failures: list[dict[str, Any]]
    case_summaries: list[dict[str, Any]]


def load_suite(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "version" not in data or "cases" not in data:
        raise ValueError(f"Invalid suite format: {path}")
    return data


def redact_question(question: str, signals: dict[str, str]) -> str:
    """Replace known tag names with opaque signal keys for safe reporting."""

    out = question
    for key, name in sorted(signals.items(), key=lambda kv: -len(kv[1])):
        if name:
            out = re.sub(re.escape(name), key, out, flags=re.IGNORECASE)
    return out


def _signal_name_index(objects: list[ControlObject]) -> dict[str, str]:
    index: dict[str, str] = {}
    for obj in objects:
        if obj.name:
            index[obj.name] = obj.id
            index[obj.name.lower()] = obj.id
    return index


def _resolve_signal_key(
    key: str,
    signals: dict[str, str],
    name_index: dict[str, str],
) -> str | None:
    name = signals.get(key)
    if not name:
        return None
    return name_index.get(name) or name_index.get(name.lower())


def _inline_synthetic_motor_graph() -> tuple[list[ControlObject], list[Relationship]]:
    def tag(name: str) -> ControlObject:
        return ControlObject(
            id=f"tag::PLC/PRG/{name}",
            name=name,
            object_type=ControlObjectType.TAG,
            source_location=f"Controller:PLC/Program:PRG/Tag:{name}",
        )

    def rung(idx: int) -> ControlObject:
        return ControlObject(
            id=f"rung::PLC/PRG/Routine_A/{idx}",
            name=f"Rung {idx}",
            object_type=ControlObjectType.RUNG,
            source_location=f"Controller:PLC/Program:PRG/Routine:Routine_A/Rung[{idx}]",
        )

    def block() -> ControlObject:
        return ControlObject(
            id="block::PLC/PRG/Generic_A",
            name="Generic_A",
            object_type=ControlObjectType.FUNCTION_BLOCK,
            source_location="Controller:PLC/Program:PRG/Routine:Routine_A/Block:1",
        )

    def read(source: str, target: str, idx: int) -> Relationship:
        return Relationship(
            id=f"rel::read::{source}::{target}::{idx}",
            source_id=source,
            target_id=f"tag::PLC/PRG/{target}",
            relationship_type=RelationshipType.READS,
            source_location=f"Controller:PLC/Program:PRG/Routine:Routine_A/Rung[{idx}]",
            confidence=ConfidenceLevel.HIGH,
            platform_specific={"instruction_type": "XIC"},
        )

    def write(source: str, target: str, idx: int) -> Relationship:
        return Relationship(
            id=f"rel::write::{source}::{target}::{idx}",
            source_id=source,
            target_id=f"tag::PLC/PRG/{target}",
            relationship_type=RelationshipType.WRITES,
            write_behavior=WriteBehaviorType.SETS_TRUE,
            source_location=f"Controller:PLC/Program:PRG/Routine:Routine_A/Rung[{idx}]",
            confidence=ConfidenceLevel.HIGH,
            platform_specific={"instruction_type": "OTE"},
        )

    objects = [
        tag("Permissive_A"),
        tag("Fault_1"),
        tag("Motor_Run"),
        tag("Valve_Open"),
        tag("No_Writer"),
        rung(1),
        rung(2),
        rung(3),
        block(),
    ]
    relationships = [
        read("rung::PLC/PRG/Routine_A/1", "Permissive_A", 1),
        read("rung::PLC/PRG/Routine_A/1", "Fault_1", 1),
        write("rung::PLC/PRG/Routine_A/1", "Motor_Run", 1),
        write("rung::PLC/PRG/Routine_A/2", "Motor_Run", 2),
        read("rung::PLC/PRG/Routine_A/3", "Motor_Run", 3),
        write("rung::PLC/PRG/Routine_A/3", "Valve_Open", 3),
        Relationship(
            id="rel::unknown::1",
            source_id="block::PLC/PRG/Generic_A",
            target_id="tag::PLC/PRG/Motor_Run",
            relationship_type=RelationshipType.REFERENCES,
            source_location="Controller:PLC/Program:PRG/Routine:Routine_A/Block:1",
            confidence=ConfidenceLevel.LOW,
            platform_specific={"binding_status": "direction_unknown"},
        ),
    ]
    return objects, relationships


_INLINE_BUILDERS: dict[str, Callable[[], tuple[list[ControlObject], list[Relationship]]]] = {
    "synthetic_motor": _inline_synthetic_motor_graph,
}


def load_fixture_graph(fixture_def: dict[str, Any]) -> tuple[list[ControlObject], list[Relationship]]:
    kind = fixture_def.get("kind")
    if kind == "inline":
        builder_id = fixture_def.get("builder")
        if builder_id not in _INLINE_BUILDERS:
            raise ValueError(f"Unknown inline builder: {builder_id}")
        return _INLINE_BUILDERS[builder_id]()
    if kind == "l5x":
        rel_path = fixture_def.get("path")
        if not rel_path:
            raise ValueError("l5x fixture requires path")
        path = _FIXTURES_L5X / rel_path
        if not path.is_file():
            raise FileNotFoundError(f"L5X fixture not found: {path}")
        project = RockwellL5XConnector().parse(path.name, path.read_bytes())
        normalized = normalize_l5x_project(project)
        return normalized["control_objects"], normalized["relationships"]
    if kind == "private_l5x":
        abs_path = fixture_def.get("absolute_path")
        if not abs_path:
            raise ValueError("private_l5x fixture requires absolute_path")
        path = Path(abs_path)
        if not path.is_file():
            raise FileNotFoundError(f"Private L5X not found: {path}")
        project = RockwellL5XConnector().parse(path.name, path.read_bytes())
        normalized = normalize_l5x_project(project)
        return normalized["control_objects"], normalized["relationships"]
    raise ValueError(f"Unsupported fixture kind: {kind}")


def _collect_upstream_names(workspace: SignalTroubleshootingWorkspace) -> set[str]:
    names: set[str] = set()
    for item in workspace.upstream_required_conditions:
        if item.target_name:
            names.add(item.target_name)
        names.update(item.condition_signal_names)
    unified = workspace.unified_evidence
    if unified:
        for cond in unified.what_controls_this_signal:
            if cond.signal_name:
                names.add(cond.signal_name)
        for dep in unified.upstream_dependencies:
            if dep.upstream_signal_name:
                names.add(dep.upstream_signal_name)
        for writer in unified.who_writes_this_signal:
            names.update(writer.condition_signal_names)
    return {n.lower() for n in names if n}


def _collect_writer_count(workspace: SignalTroubleshootingWorkspace) -> int:
    if workspace.unified_evidence and workspace.unified_evidence.who_writes_this_signal:
        return len(workspace.unified_evidence.who_writes_this_signal)
    return len(workspace.writer_rungs)


def _has_st_writer(workspace: SignalTroubleshootingWorkspace) -> bool:
    unified = workspace.unified_evidence
    if not unified:
        return False
    return any(
        w.source_provenance.originating_language == "structured_text"
        for w in unified.who_writes_this_signal
    )


def _aoi_parameter_names(workspace: SignalTroubleshootingWorkspace) -> set[str]:
    unified = workspace.unified_evidence
    if not unified:
        return set()
    return {g.parameter_name for g in unified.verification.aoi if g.parameter_name}


def _collect_downstream_names(workspace: SignalTroubleshootingWorkspace) -> set[str]:
    names: set[str] = set()
    for item in workspace.downstream_readers:
        if item.source_name:
            names.add(item.source_name.lower())
    unified = workspace.unified_evidence
    if unified:
        for reader in unified.where_is_it_used:
            if reader.signal_name:
                names.add(reader.signal_name.lower())
        for reader in unified.downstream_impact:
            if reader.signal_name:
                names.add(reader.signal_name.lower())
    return names


def _writer_languages(workspace: SignalTroubleshootingWorkspace) -> set[str]:
    unified = workspace.unified_evidence
    if not unified:
        return set()
    langs: set[str] = set()
    for w in unified.who_writes_this_signal:
        langs.add(w.source_provenance.originating_language)
    return langs


def _explanation_is_substantive(workspace: SignalTroubleshootingWorkspace) -> bool:
    text = (workspace.deterministic_explanation or "").strip()
    if len(text) < 40:
        return False
    vague_only = text.lower().startswith("i could not") or text.lower().startswith("no matching")
    if vague_only:
        return False
    unified = workspace.unified_evidence
    if unified and unified.who_writes_this_signal and "writer" not in text.lower():
        return len(text) >= 80
    return True


def score_case(
    *,
    case_id: str,
    fixture_id: str,
    question: str,
    expect: dict[str, Any],
    signals: dict[str, str],
    workspace: SignalTroubleshootingWorkspace,
    objects: list[ControlObject],
) -> CaseResult:
    name_index = _signal_name_index(objects)
    checks: list[CheckResult] = []

    def add(name: str, passed: bool, expected: Any, actual: Any) -> None:
        checks.append(CheckResult(name=name, passed=passed, expected=expected, actual=actual))

    target_key = expect.get("target_signal")
    if target_key:
        expected_name = signals.get(target_key)
        actual_name = workspace.target_signal.name if workspace.target_signal else None
        resolved = (
            expected_name is not None
            and actual_name is not None
            and expected_name.lower() == actual_name.lower()
        )
        add("target_resolution", resolved, target_key, bool(actual_name))

    intent = expect.get("intent")
    if intent:
        actual_intent = workspace.interpretation.intent
        add("intent", actual_intent == intent, intent, actual_intent)

    if "writer_count_min" in expect or "writer_count_max" in expect:
        count = _collect_writer_count(workspace)
        min_c = expect.get("writer_count_min", 0)
        max_c = expect.get("writer_count_max", count)
        ok = min_c <= count <= max_c
        add("writer_count", ok, {"min": min_c, "max": max_c}, count)

    upstream_keys = expect.get("upstream_signals") or []
    if upstream_keys:
        expected_names = {
            signals[k].lower()
            for k in upstream_keys
            if k in signals and signals[k]
        }
        found = _collect_upstream_names(workspace)
        recall = len(expected_names & found) / len(expected_names) if expected_names else 1.0
        min_recall = expect.get("upstream_recall_min", 1.0)
        add("upstream_recall", recall >= min_recall, min_recall, round(recall, 3))

    if "downstream_count_min" in expect or "downstream_count_max" in expect:
        count = len(workspace.downstream_readers)
        min_c = expect.get("downstream_count_min", 0)
        max_c = expect.get("downstream_count_max", count)
        ok = min_c <= count <= max_c
        add("downstream_count", ok, {"min": min_c, "max": max_c}, count)

    downstream_keys = expect.get("downstream_signals") or []
    if downstream_keys:
        expected_names = {
            signals[k].lower()
            for k in downstream_keys
            if k in signals and signals[k]
        }
        found = _collect_downstream_names(workspace)
        recall = len(expected_names & found) / len(expected_names) if expected_names else 1.0
        min_recall = expect.get("downstream_recall_min", 1.0)
        add("downstream_recall", recall >= min_recall, min_recall, round(recall, 3))

    if "unknown_direction_count_min" in expect or "unknown_direction_count_max" in expect:
        count = len(workspace.unknown_direction_blocks)
        min_c = expect.get("unknown_direction_count_min", 0)
        max_c = expect.get("unknown_direction_count_max", count)
        ok = min_c <= count <= max_c
        add("unknown_direction_count", ok, {"min": min_c, "max": max_c}, count)

    if expect.get("unified_has_st_writer"):
        add("unified_st_writer", _has_st_writer(workspace), True, _has_st_writer(workspace))

    aoi_param = expect.get("aoi_parameter_present")
    if aoi_param:
        params = _aoi_parameter_names(workspace)
        add("aoi_parameter", aoi_param in params, aoi_param, sorted(params))

    if "min_confidence" in expect:
        conf = workspace.confidence_summary.confidence
        threshold = expect["min_confidence"]
        add("confidence", conf >= threshold, threshold, round(conf, 3))

    if expect.get("target_must_resolve") is False:
        add("target_unresolved", workspace.target_signal is None, False, workspace.target_signal is None)
    elif expect.get("no_writers_expected"):
        add("no_writers", _collect_writer_count(workspace) == 0, 0, _collect_writer_count(workspace))

    if expect.get("require_ladder_provenance"):
        unified = workspace.unified_evidence
        has_ladder = bool(unified and unified.verification.ladder)
        add("missing_ladder_provenance", has_ladder, True, has_ladder)

    if expect.get("require_fbd_provenance"):
        unified = workspace.unified_evidence
        has_fbd = bool(unified and unified.verification.fbd)
        add("missing_fbd_provenance", has_fbd, True, has_fbd)

    if expect.get("require_aoi_provenance"):
        unified = workspace.unified_evidence
        has_aoi = bool(unified and unified.verification.aoi)
        add("missing_aoi_provenance", has_aoi, True, has_aoi)

    if expect.get("require_substantive_explanation"):
        ok = _explanation_is_substantive(workspace)
        add("explanation_quality", ok, True, ok)

    passed = all(c.passed for c in checks)
    return CaseResult(
        case_id=case_id,
        fixture_id=fixture_id,
        passed=passed,
        checks=checks,
        redacted_question=redact_question(question, signals),
    )


def _invoke_workspace(
    *,
    mode: EvalMode,
    question: str,
    objects: list[ControlObject],
    relationships: list[Relationship],
    project_id: str | None = None,
) -> SignalTroubleshootingWorkspace:
    if mode == "direct":
        return build_signal_workspace(
            question=question,
            control_objects=objects,
            relationships=relationships,
        )
    if mode == "route":
        from app.api.routes import (
            TroubleshootQuestionRequest,
            resolve_troubleshoot_workspace,
        )
        from app.models.control_model import ControlProject
        from app.services.project_store import project_store

        if not project_id:
            project_id = hashlib.sha256(b"troubleshoot_eval_route").hexdigest()[:16]
        stub = ControlProject(
            project_name="troubleshoot_eval",
            file_name="eval_stub.L5X",
            connector="rockwell_l5x",
            file_hash=project_id,
            controllers=[],
        )
        project_store.save(stub)
        project_store.seed_normalized(
            project_id,
            {
                "control_objects": objects,
                "relationships": relationships,
                "execution_contexts": [],
            },
        )
        return resolve_troubleshoot_workspace(
            TroubleshootQuestionRequest(
                project_id=project_id,
                question=question,
                use_live_data=False,
            )
        )
    raise ValueError(f"Unsupported eval mode: {mode}")


def run_suite(
    suite: dict[str, Any],
    *,
    mode: EvalMode = "direct",
) -> AggregateReport:
    fixtures = suite.get("fixtures", {})
    cases = suite["cases"]
    case_results: list[CaseResult] = []

    for case in cases:
        fixture_id = case["fixture"]
        fixture_def = fixtures[fixture_id]
        signals = fixture_def.get("signals", {})
        objects, relationships = load_fixture_graph(fixture_def)
        workspace = _invoke_workspace(
            mode=mode,
            question=case["question"],
            objects=objects,
            relationships=relationships,
        )
        case_results.append(
            score_case(
                case_id=case["id"],
                fixture_id=fixture_id,
                question=case["question"],
                expect=case.get("expect", {}),
                signals=signals,
                workspace=workspace,
                objects=objects,
            )
        )

    return _aggregate(suite.get("version", 1), mode, case_results, cases)


def _aggregate(
    version: int,
    mode: EvalMode,
    results: list[CaseResult],
    raw_cases: list[dict[str, Any]],
) -> AggregateReport:
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    check_names = sorted({c.name for r in results for c in r.checks})

    metrics: dict[str, float] = {}
    for name in check_names:
        relevant = [c for r in results for c in r.checks if c.name == name]
        if relevant:
            metrics[f"{name}_pass_rate"] = round(
                sum(1 for c in relevant if c.passed) / len(relevant), 4
            )

    by_fixture: dict[str, dict[str, float]] = {}
    for fixture_id in sorted({r.fixture_id for r in results}):
        subset = [r for r in results if r.fixture_id == fixture_id]
        by_fixture[fixture_id] = {
            "cases": float(len(subset)),
            "pass_rate": round(sum(1 for r in subset if r.passed) / len(subset), 4)
            if subset
            else 0.0,
        }

    by_intent: dict[str, dict[str, float]] = {}
    case_intent = {c["id"]: c.get("expect", {}).get("intent", "unspecified") for c in raw_cases}
    for intent in sorted(set(case_intent.values())):
        ids = {cid for cid, it in case_intent.items() if it == intent}
        subset = [r for r in results if r.case_id in ids]
        if subset:
            by_intent[intent] = {
                "cases": float(len(subset)),
                "pass_rate": round(sum(1 for r in subset if r.passed) / len(subset), 4),
            }

    failures = [
        {
            "case_id": r.case_id,
            "fixture_id": r.fixture_id,
            "failed_checks": [
                {
                    "check": c.name,
                    "category": CHECK_TO_FAILURE_CATEGORY.get(c.name, c.name),
                    "expected": c.expected,
                    "actual": c.actual,
                }
                for c in r.checks
                if not c.passed
            ],
        }
        for r in results
        if not r.passed
    ]

    failure_categories: dict[str, int] = {}
    for failure in failures:
        for fc in failure["failed_checks"]:
            cat = fc["category"]
            failure_categories[cat] = failure_categories.get(cat, 0) + 1

    recommended_next_fixes = _recommend_fixes(failure_categories, metrics)

    summaries = [
        {
            "case_id": r.case_id,
            "fixture_id": r.fixture_id,
            "passed": r.passed,
            "redacted_question": r.redacted_question,
            "checks_passed": sum(1 for c in r.checks if c.passed),
            "checks_total": len(r.checks),
        }
        for r in results
    ]

    return AggregateReport(
        version=version,
        mode=mode,
        total_cases=total,
        passed_cases=passed,
        failed_cases=total - passed,
        pass_rate=round(passed / total, 4) if total else 0.0,
        metrics=metrics,
        by_fixture=by_fixture,
        by_intent=by_intent,
        failure_categories=failure_categories,
        recommended_next_fixes=recommended_next_fixes,
        failures=failures,
        case_summaries=summaries,
    )


def _recommend_fixes(
    failure_categories: dict[str, int],
    metrics: dict[str, float],
) -> list[str]:
    fixes: list[str] = []
    ranked = sorted(failure_categories.items(), key=lambda kv: -kv[1])
    for cat, _count in ranked[:5]:
        if cat == "target_resolution":
            fixes.append(
                "Improve fuzzy tag matching and member-path resolution for long UDT/AOI names."
            )
        elif cat == "intent_detection":
            fixes.append(
                "Expand intent phrase patterns for where-written/read and upstream/downstream questions."
            )
        elif cat == "writer_count":
            fixes.append(
                "Reconcile unified writer aggregation with ladder/FBD/AOI/ST edge normalization."
            )
        elif cat == "upstream_recall":
            fixes.append(
                "Strengthen upstream condition extraction from branch logic and FBD input pins."
            )
        elif cat == "downstream_recall":
            fixes.append(
                "Improve downstream reader discovery across routines and ST boolean expressions."
            )
        elif cat == "missing_ladder_provenance":
            fixes.append(
                "Ensure ladder writers populate verification.ladder with rung numbers and instruction types."
            )
        elif cat == "missing_fbd_provenance":
            fixes.append(
                "Complete FBD Phase 1 pin/wire provenance so verification.fbd groups appear for FBD writers."
            )
        elif cat == "missing_aoi_provenance":
            fixes.append(
                "Map AOI instance CALL edges to verification.aoi parameter groups (In/Out)."
            )
        elif cat == "unknown_direction_handling":
            fixes.append(
                "Register vendor AMP blocks and unresolved FBD references with deterministic parameter bindings."
            )
        elif cat == "vague_or_incomplete_explanation":
            fixes.append(
                "Enrich deterministic explanations with writer counts, verification language, and condition summaries."
            )
    if not fixes and metrics:
        low = [k for k, v in metrics.items() if v < 1.0]
        if low:
            fixes.append(f"Investigate low pass-rate checks: {', '.join(sorted(low)[:3])}.")
    if not fixes:
        fixes.append("Maintain current pass rate; add regression cases as normalization improves.")
    return fixes[:5]


def report_to_dict(report: AggregateReport) -> dict[str, Any]:
    """Serialize aggregate report without tag/rung names."""

    return {
        "harness": "troubleshoot_eval",
        "version": report.version,
        "mode": report.mode,
        "total_cases": report.total_cases,
        "passed_cases": report.passed_cases,
        "failed_cases": report.failed_cases,
        "pass_rate": report.pass_rate,
        "metrics": report.metrics,
        "by_fixture": report.by_fixture,
        "by_intent": report.by_intent,
        "failure_categories": report.failure_categories,
        "top_failure_categories": sorted(
            report.failure_categories.items(),
            key=lambda kv: -kv[1],
        )[:3],
        "recommended_next_fixes": report.recommended_next_fixes,
        "failures": report.failures,
        "case_summaries": report.case_summaries,
    }
