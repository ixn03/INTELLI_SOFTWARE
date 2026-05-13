"use client";

import { useCallback, useMemo, useState } from "react";

import type {
  EvidenceBundle,
  LLMAssistResponse,
  NormalizedControlObjectSummary,
  SequenceSemanticSummary,
  TraceConclusion,
  TraceRelationship,
  TraceResponse,
} from "@/types/reasoning";
import {
  asRecord,
  evidenceBundleFromTrace,
  getStringMeta,
  getTraceV2Kind,
  isDesignTraceConclusion,
  isRuntimeV2Trace,
  parseEvidenceBundle,
  parseTrustAssessment,
  trustFromTrace,
} from "@/types/reasoning";

import {
  Accordion,
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  Code,
  ConfidenceBadge,
  EmptyState,
  InlineError,
  KVRow,
  LoadingLine,
  Stat,
} from "./ui";

import {
  OperationalVerdictCard,
  RuntimeConditionBreakdown,
  RuntimeConflictsBanner,
  RuntimeSnapshotPanel,
  WriterPathResultsSection,
} from "./RuntimeDiagnosisView";

/**
 * Main panel -- shows the answer for the currently selected target.
 *
 * Layered top to bottom:
 *
 *   1. Selected-object card + trace actions.
 *   2. Runtime snapshot panel (diagnosis mode).
 *   3. Operational verdict card when ``trace_version === "runtime_v2"``.
 *   4. Design trace answer (natural-language Trace v2, excluding runtime
 *      overlay conclusions).
 *   5. Key conditions (writer_conditions / branch_warning).
 *   6. Runtime condition breakdown, writer paths, conflicts (runtime v2).
 *   7. Writers / readers count strip.
 *   8. Evidence accordion.
 *   9. Debug accordion (raw JSON hidden until expanded).
 *
 * Trace v2 is the default. A small "Trace v1" link in the header
 * triggers the raw v1 endpoint for advanced users.
 */

type TraceVersion = "v1" | "v2";

interface AnswerViewProps {
  selectedObject: NormalizedControlObjectSummary | null;
  selectedObjectId: string;

  trace: TraceResponse | null;
  traceVersion: TraceVersion | null;
  traceLoading: TraceVersion | null;
  traceError: string | null;
  /** Set when the most recent trace was triggered via /api/ask-v1. */
  askedQuestion: string | null;

  onRunTrace: (version: TraceVersion) => void;

  /** Runtime v2 snapshot JSON (controlled). */
  runtimeSnapshotText: string;
  onRuntimeSnapshotTextChange: (text: string) => void;
  onEvaluateRuntimeV2: (snapshot: Record<string, unknown>) => void;
  runtimeEvaluating: boolean;
  runtimeEvalError: string | null;

  /** Present after a successful ``/api/ask-v3`` (deterministic-first assist). */
  llmAssist: LLMAssistResponse | null;
}

export default function AnswerView(props: AnswerViewProps) {
  const {
    selectedObject,
    selectedObjectId,
    trace,
    traceVersion,
    traceLoading,
    traceError,
    askedQuestion,
    onRunTrace,
    runtimeSnapshotText,
    onRuntimeSnapshotTextChange,
    onEvaluateRuntimeV2,
    runtimeEvaluating,
    runtimeEvalError,
    llmAssist,
  } = props;

  const [runtimeParseError, setRuntimeParseError] = useState<string | null>(
    null,
  );

  const runtimePanelDisabled =
    !selectedObjectId.trim() || traceLoading !== null;

  const handleRuntimeEvaluate = useCallback(() => {
    setRuntimeParseError(null);
    const trimmed = runtimeSnapshotText.trim();
    if (!trimmed) {
      setRuntimeParseError("Enter a JSON object with tag values.");
      return;
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(trimmed) as unknown;
    } catch {
      setRuntimeParseError("Runtime snapshot JSON is invalid.");
      return;
    }
    if (
      parsed === null ||
      typeof parsed !== "object" ||
      Array.isArray(parsed)
    ) {
      setRuntimeParseError(
        "Runtime snapshot must be a JSON object (not an array or primitive).",
      );
      return;
    }
    void onEvaluateRuntimeV2(parsed as Record<string, unknown>);
  }, [runtimeSnapshotText, onEvaluateRuntimeV2]);

  return (
    <main className="flex h-full min-w-0 flex-1 flex-col gap-6 overflow-y-auto px-8 py-8">
      <SelectedObjectCard
        selectedObject={selectedObject}
        selectedObjectId={selectedObjectId}
        trace={trace}
        traceVersion={traceVersion}
        traceLoading={traceLoading}
        onRunTrace={onRunTrace}
      />

      <RuntimeSnapshotPanel
        value={runtimeSnapshotText}
        onChange={(t) => {
          onRuntimeSnapshotTextChange(t);
          if (runtimeParseError) setRuntimeParseError(null);
        }}
        onEvaluate={handleRuntimeEvaluate}
        disabled={runtimePanelDisabled}
        evaluating={runtimeEvaluating}
        parseError={runtimeParseError}
        apiError={runtimeEvalError}
      />

      {traceError ? <InlineError>{traceError}</InlineError> : null}

      {traceLoading && !trace ? (
        <Card>
          <CardBody>
            <LoadingLine>
              Running {traceLoading === "v2" ? "Trace v2" : "Trace v1"}...
            </LoadingLine>
          </CardBody>
        </Card>
      ) : null}

      {runtimeEvaluating && trace ? (
        <Card>
          <CardBody>
            <LoadingLine>Evaluating runtime snapshot…</LoadingLine>
          </CardBody>
        </Card>
      ) : null}

      {trace ? (
        <>
          {llmAssist ? (
            <LlmAssistNaturalCard assist={llmAssist} askedQuestion={askedQuestion} />
          ) : null}

          {askedQuestion ? (
            <RouterPill trace={trace} askedQuestion={askedQuestion} />
          ) : null}

          {isRuntimeV2Trace(trace) ? <OperationalVerdictCard trace={trace} /> : null}

          {llmAssist ? (
            <LlmAssistSuggestedCard assist={llmAssist} trace={trace} />
          ) : null}

          {llmAssist ? <LlmAssistStructuredEvidence assist={llmAssist} /> : null}

          <TrustAssessmentCard trace={trace} assist={llmAssist} />
          <WhyIntelliThinksThisCard trace={trace} assist={llmAssist} />
          {llmAssist ? <SequenceSemanticsCard assist={llmAssist} /> : null}

          <PrimaryAnswerCard
            trace={trace}
            variant={llmAssist ? "assist_secondary" : "default"}
          />
          <ConditionsCard trace={trace} />

          {isRuntimeV2Trace(trace) ? (
            <>
              <RuntimeConditionBreakdown trace={trace} />
              <WriterPathResultsSection trace={trace} />
              <RuntimeConflictsBanner trace={trace} />
            </>
          ) : null}

          <CountsStrip trace={trace} />
          <EvidenceAccordion trace={trace} />
          <DebugAccordion trace={trace} />
        </>
      ) : null}

      {!trace && !traceLoading && !traceError ? (
        <Card>
          <CardBody>
            <EmptyState
              title="Choose an object to begin"
              hint="Use the sidebar to find a control object, then run Trace v2 — or ask a question."
            />
          </CardBody>
        </Card>
      ) : null}
    </main>
  );
}

// ===========================================================================
// 1. Selected object card -- always visible header card for the main panel.
// ===========================================================================

function SelectedObjectCard({
  selectedObject,
  selectedObjectId,
  trace,
  traceVersion,
  traceLoading,
  onRunTrace,
}: {
  selectedObject: NormalizedControlObjectSummary | null;
  selectedObjectId: string;
  trace: TraceResponse | null;
  traceVersion: TraceVersion | null;
  traceLoading: TraceVersion | null;
  onRunTrace: (version: TraceVersion) => void;
}) {
  const targetReady = Boolean(selectedObjectId.trim());
  const displayName =
    selectedObject?.name ??
    (selectedObjectId ? selectedObjectId.split("/").pop() ?? selectedObjectId : null);

  return (
    <Card>
      <CardHeader
        eyebrow="Selected object"
        title={
          displayName ? (
            <span className="text-base text-zinc-50">{displayName}</span>
          ) : (
            <span className="text-zinc-500">No object selected</span>
          )
        }
        trailing={
          <div className="flex shrink-0 items-center gap-2">
            <Button
              tone="primary"
              disabled={!targetReady || traceLoading !== null}
              onClick={() => onRunTrace("v2")}
              title="Natural-language, condition-aware trace"
            >
              {traceLoading === "v2" ? "Tracing..." : "Trace v2"}
            </Button>
            <Button
              tone="ghost"
              disabled={!targetReady || traceLoading !== null}
              onClick={() => onRunTrace("v1")}
              title="Raw dependency graph (advanced)"
            >
              {traceLoading === "v1" ? "..." : "v1"}
            </Button>
          </div>
        }
      />
      <CardBody className="space-y-2">
        {selectedObject ? (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone="outline" uppercase>
                {selectedObject.object_type}
              </Badge>
              {trace?.confidence ? (
                <ConfidenceBadge value={trace.confidence} />
              ) : null}
              {traceVersion ? (
                <Badge tone="info" uppercase>
                  trace {traceVersion}
                </Badge>
              ) : null}
              {trace && isRuntimeV2Trace(trace) ? (
                <Badge tone="success" uppercase>
                  runtime v2
                </Badge>
              ) : null}
            </div>
            <KVRow k="id" v={selectedObject.id} mono breakAll />
            {selectedObject.source_location ? (
              <KVRow
                k="source"
                v={selectedObject.source_location}
                mono
                breakAll
              />
            ) : null}
          </>
        ) : selectedObjectId ? (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone="outline" uppercase>
                manual id
              </Badge>
            </div>
            <KVRow k="id" v={selectedObjectId} mono breakAll />
            <p className="text-xs text-zinc-500">
              Object metadata isn&apos;t loaded yet. Load the object list
              from the sidebar to view its source location and type.
            </p>
          </>
        ) : (
          <p className="text-sm text-zinc-500">
            Use the sidebar to pick a control object, or ask INTELLI a
            question.
          </p>
        )}
      </CardBody>
    </Card>
  );
}

// ===========================================================================
// Router pill: shown when the trace was triggered by /api/ask-v1.
// ===========================================================================

function RouterPill({
  trace,
  askedQuestion,
}: {
  trace: TraceResponse;
  askedQuestion: string;
}) {
  const intent = getStringMeta(trace.platform_specific, "detected_intent");
  const detected = getStringMeta(
    trace.platform_specific,
    "detected_target_object_id",
  );
  return (
    <Card>
      <CardBody className="flex flex-wrap items-baseline justify-between gap-3 py-3">
        <p className="text-sm text-zinc-300">
          <span className="text-zinc-500">You asked: </span>
          <span className="italic text-zinc-100">
            &ldquo;{askedQuestion}&rdquo;
          </span>
        </p>
        <div className="flex flex-wrap items-center gap-1.5">
          {intent ? <Badge tone="info">intent: {intent}</Badge> : null}
          {detected ? <Badge tone="neutral">target identified</Badge> : null}
        </div>
      </CardBody>
    </Card>
  );
}

// --- Ask v3 (LLM Assist) presentation ------------------------------------

function LlmAssistNaturalCard({
  assist,
  askedQuestion,
}: {
  assist: LLMAssistResponse;
  askedQuestion: string | null;
}) {
  const conf = assist.confidence?.toLowerCase() ?? "low";
  const tone: "success" | "warning" | "danger" | "neutral" =
    conf === "high"
      ? "success"
      : conf === "medium"
        ? "warning"
        : conf === "low"
          ? "danger"
          : "neutral";
  return (
    <Card>
      <CardHeader eyebrow="Assist answer" title="Engineering summary" />
      <CardBody className="space-y-4">
        {askedQuestion ? (
          <p className="text-xs text-zinc-500">
            <span className="text-zinc-600">Question: </span>
            <span className="italic text-zinc-300">&ldquo;{askedQuestion}&rdquo;</span>
          </p>
        ) : null}
        <p className="text-xl font-medium leading-snug tracking-tight text-zinc-50">
          {assist.answer}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={tone} uppercase>
            confidence: {assist.confidence}
          </Badge>
          <Badge tone="info" uppercase>
            intent: {assist.detected_intent}
          </Badge>
          {assist.target_object_id ? (
            <Badge tone="neutral" uppercase>
              target id
            </Badge>
          ) : null}
        </div>
        {assist.target_object_id ? (
          <p className="break-all font-mono text-xs text-zinc-400">
            {assist.target_object_id}
          </p>
        ) : null}
      </CardBody>
    </Card>
  );
}

function LlmAssistSuggestedCard({
  assist,
  trace,
}: {
  assist: LLMAssistResponse;
  trace: TraceResponse;
}) {
  const warn = assist.warnings.filter(
    (w) => !w.includes("llm_assist_disabled"),
  );
  const checks = trace.recommended_checks ?? [];
  if (warn.length === 0 && checks.length === 0) return null;
  return (
    <Card>
      <CardHeader eyebrow="Next steps" title="Suggested checks" />
      <CardBody className="space-y-3">
        {warn.length > 0 ? (
          <ul className="list-disc space-y-1 pl-5 text-sm text-amber-100/95">
            {warn.map((w, i) => (
              <li key={`w-${i}`}>{w}</li>
            ))}
          </ul>
        ) : null}
        {checks.length > 0 ? (
          <ul className="list-disc space-y-1 pl-5 text-sm text-zinc-200">
            {checks.map((c, i) => (
              <li key={`c-${i}`}>{c}</li>
            ))}
          </ul>
        ) : null}
      </CardBody>
    </Card>
  );
}

function describeUnknown(v: unknown): string {
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  const o = asRecord(v);
  if (o) {
    for (const key of [
      "natural_language",
      "statement",
      "reason",
      "detail",
      "condition_summary",
      "target_state",
      "runtime_value",
    ]) {
      const value = o[key];
      if (typeof value === "string" && value.trim()) return value;
      if (typeof value === "number" || typeof value === "boolean") {
        return `${key}: ${String(value)}`;
      }
    }
    return JSON.stringify(o);
  }
  return String(v);
}

function asTextLines(v: unknown): string[] {
  if (v === null || v === undefined) return [];
  if (Array.isArray(v)) {
    return v.map(describeUnknown).filter(Boolean);
  }
  if (typeof v === "string" && v.trim()) return [v.trim()];
  if (typeof v === "object") return [describeUnknown(v)];
  return [];
}

function LlmAssistStructuredEvidence({ assist }: { assist: LLMAssistResponse }) {
  const ev = assist.evidence_used;
  const blocking = asTextLines(ev.blocking_conditions);
  const satisfied = asTextLines(ev.satisfied_conditions);
  const missing = asTextLines(ev.missing_conditions);
  const unsupported = asTextLines(ev.unsupported_conditions);
  const seq = asTextLines(ev.sequence_summary);
  const semantics = asRecord(ev.sequence_semantics);
  const waiting = asTextLines(semantics?.likely_waiting_conditions);
  const faults = asTextLines(semantics?.fault_conditions);
  const manual = asTextLines(semantics?.manual_override_conditions);
  const kn = asTextLines(ev.knowledge_notes);
  const conc = asTextLines(ev.deterministic_conclusions);
  const count =
    blocking.length +
    satisfied.length +
    missing.length +
    unsupported.length +
    seq.length +
    waiting.length +
    faults.length +
    manual.length +
    kn.length +
    conc.length;
  if (count === 0) return null;
  return (
    <Accordion
      eyebrow="Evidence"
      title="Structured deterministic findings"
      count={count}
      defaultOpen={false}
    >
      <div className="space-y-4 text-sm text-zinc-200">
        {blocking.length > 0 ? (
          <EvidenceList title="Blocking conditions" lines={blocking} tone="rose" />
        ) : null}
        {satisfied.length > 0 ? (
          <EvidenceList title="Satisfied conditions" lines={satisfied} tone="emerald" />
        ) : null}
        {missing.length > 0 ? (
          <EvidenceList title="Missing runtime values" lines={missing} tone="amber" />
        ) : null}
        {unsupported.length > 0 ? (
          <EvidenceList
            title="Unsupported / too complex"
            lines={unsupported}
            tone="zinc"
          />
        ) : null}
        {seq.length > 0 ? (
          <EvidenceList title="Sequence / state" lines={seq} tone="sky" />
        ) : null}
        {waiting.length > 0 ? (
          <EvidenceList title="Waiting / completion evidence" lines={waiting} tone="amber" />
        ) : null}
        {faults.length > 0 ? (
          <EvidenceList title="Fault / interlock evidence" lines={faults} tone="rose" />
        ) : null}
        {manual.length > 0 ? (
          <EvidenceList title="Manual / auto evidence" lines={manual} tone="sky" />
        ) : null}
        {kn.length > 0 ? (
          <EvidenceList title="Knowledge notes" lines={kn} tone="zinc" />
        ) : null}
        {conc.length > 0 ? (
          <EvidenceList title="Deterministic conclusions" lines={conc} tone="zinc" />
        ) : null}
      </div>
    </Accordion>
  );
}

function TrustAssessmentCard({
  trace,
  assist,
}: {
  trace: TraceResponse;
  assist: LLMAssistResponse | null;
}) {
  const fromAssist = parseTrustAssessment(assist?.evidence_used?.trust_assessment);
  const trust = fromAssist ?? trustFromTrace(trace);
  if (!trust) return null;
  const reasons = [
    ...trust.uncertainty_reasons,
    ...trust.unsupported_reasons,
    ...trust.conflicting_reasons,
    ...trust.missing_runtime_reasons,
    ...trust.parser_coverage_reasons,
  ];
  const tone: "success" | "warning" | "danger" =
    trust.confidence_score >= 0.8
      ? "success"
      : trust.confidence_score >= 0.55
        ? "warning"
        : "danger";
  return (
    <Card>
      <CardHeader
        eyebrow="Confidence"
        title="Trustworthiness assessment"
        trailing={
          <Badge tone={tone} uppercase>
            {Math.round(trust.confidence_score * 100)}%
          </Badge>
        }
      />
      <CardBody className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={tone} uppercase>
            {trust.recommendation_level.replaceAll("_", " ")}
          </Badge>
          <span className="text-xs text-zinc-500">
            Deterministic evidence only. Lower confidence means INTELLI found
            missing, conflicting, unsupported, or parser-limited evidence.
          </span>
        </div>
        {reasons.length > 0 ? (
          <ul className="list-disc space-y-1 pl-5 text-sm text-zinc-300">
            {reasons.slice(0, 10).map((reason, i) => (
              <li key={i}>{reason}</li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-zinc-400">
            No conflicts, unsupported evidence, or missing runtime values were
            reported for this answer.
          </p>
        )}
      </CardBody>
    </Card>
  );
}

function WhyIntelliThinksThisCard({
  trace,
  assist,
}: {
  trace: TraceResponse;
  assist: LLMAssistResponse | null;
}) {
  const fromAssist = parseEvidenceBundle(assist?.evidence_used?.evidence_bundle);
  const bundle = fromAssist ?? evidenceBundleFromTrace(trace);
  if (!bundle) return null;
  return (
    <Card>
      <CardHeader
        eyebrow="Why INTELLI thinks this"
        title="Evidence bundle"
        trailing={
          <Badge tone={bundle.confidence >= 0.75 ? "success" : bundle.confidence >= 0.5 ? "warning" : "danger"} uppercase>
            {Math.round(bundle.confidence * 100)}%
          </Badge>
        }
      />
      <CardBody className="space-y-4">
        <p className="text-sm text-zinc-200">{bundle.conclusion}</p>
        <EvidenceItemsSection
          title="Supporting evidence"
          items={bundle.supporting_evidence}
          tone="emerald"
        />
        <EvidenceItemsSection
          title="Conflicting evidence"
          items={bundle.conflicting_evidence}
          tone="rose"
        />
        <EvidenceItemsSection
          title="Unsupported evidence"
          items={bundle.unsupported_evidence}
          tone="amber"
        />
        {bundle.warnings.length > 0 ? (
          <EvidenceList title="Evidence warnings" lines={bundle.warnings} tone="amber" />
        ) : null}
      </CardBody>
    </Card>
  );
}

function EvidenceItemsSection({
  title,
  items,
  tone,
}: {
  title: string;
  items: EvidenceBundle["supporting_evidence"];
  tone: "emerald" | "rose" | "amber";
}) {
  if (items.length === 0) return null;
  const border =
    tone === "emerald"
      ? "border-emerald-900/40"
      : tone === "rose"
        ? "border-rose-900/40"
        : "border-amber-900/40";
  return (
    <div className={`rounded-lg border ${border} bg-zinc-950/40 px-3 py-2`}>
      <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
        {title}
      </p>
      <ul className="space-y-2">
        {items.slice(0, 8).map((item) => (
          <li key={item.id || item.statement} className="text-sm text-zinc-200">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone="outline">{item.evidence_type}</Badge>
              <Badge tone={item.unsupported ? "warning" : "neutral"}>
                {Math.round(item.confidence * 100)}%
              </Badge>
              {item.deterministic ? <Badge tone="success">deterministic</Badge> : null}
            </div>
            <p className="mt-1">{item.statement}</p>
            {item.source_location ? (
              <p className="mt-1 break-all font-mono text-[11px] text-zinc-500">
                {item.source_location}
              </p>
            ) : null}
            {item.runtime_snapshot_keys.length > 0 ? (
              <p className="mt-1 text-xs text-zinc-500">
                Runtime keys: {item.runtime_snapshot_keys.join(", ")}
              </p>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function SequenceSemanticsCard({ assist }: { assist: LLMAssistResponse }) {
  const raw = asRecord(assist.evidence_used.sequence_semantics);
  if (!raw) return null;
  const summary = raw as unknown as SequenceSemanticSummary;
  const transitionCount = Array.isArray(summary.transition_conditions)
    ? summary.transition_conditions.length
    : 0;
  const waiting = asTextLines(summary.likely_waiting_conditions);
  const faults = asTextLines(summary.fault_conditions);
  const manual = asTextLines(summary.manual_override_conditions);
  const unsupported = asTextLines(summary.unsupported_patterns);
  if (transitionCount === 0 && waiting.length === 0 && faults.length === 0 && manual.length === 0 && unsupported.length === 0) {
    return null;
  }
  return (
    <Accordion
      eyebrow="Sequence semantics"
      title="Operational sequence evidence"
      count={transitionCount + waiting.length + faults.length + manual.length + unsupported.length}
      defaultOpen={false}
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={summary.confidence >= 0.75 ? "success" : "warning"} uppercase>
            confidence {Math.round((summary.confidence ?? 0) * 100)}%
          </Badge>
          <Badge tone="info">{transitionCount} transition conditions</Badge>
        </div>
        {waiting.length > 0 ? (
          <EvidenceList title="Likely waiting / completion" lines={waiting} tone="amber" />
        ) : null}
        {faults.length > 0 ? (
          <EvidenceList title="Fault-driven / interlock" lines={faults} tone="rose" />
        ) : null}
        {manual.length > 0 ? (
          <EvidenceList title="Operator / auto-manual" lines={manual} tone="sky" />
        ) : null}
        {unsupported.length > 0 ? (
          <EvidenceList title="Unsupported sequence patterns" lines={unsupported} tone="zinc" />
        ) : null}
      </div>
    </Accordion>
  );
}

function EvidenceList({
  title,
  lines,
  tone,
}: {
  title: string;
  lines: string[];
  tone: "rose" | "emerald" | "amber" | "sky" | "zinc";
}) {
  const border =
    tone === "rose"
      ? "border-rose-900/40"
      : tone === "emerald"
        ? "border-emerald-900/40"
        : tone === "amber"
          ? "border-amber-900/40"
          : tone === "sky"
            ? "border-sky-900/40"
            : "border-zinc-800/80";
  return (
    <div className={`rounded-lg border ${border} bg-zinc-950/40 px-3 py-2`}>
      <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
        {title}
      </p>
      <ul className="list-disc space-y-1 pl-4 text-zinc-200">
        {lines.map((line, i) => (
          <li key={i}>{line}</li>
        ))}
      </ul>
    </div>
  );
}

// ===========================================================================
// 2. Primary answer card -- the natural-language hero.
// ===========================================================================

function PrimaryAnswerCard({
  trace,
  variant = "default",
}: {
  trace: TraceResponse;
  variant?: "default" | "assist_secondary";
}) {
  const v2Conclusions = useMemo(
    () =>
      trace.conclusions.filter(
        (c) =>
          isDesignTraceConclusion(c) && getTraceV2Kind(c) !== null,
      ),
    [trace.conclusions],
  );

  const heroStatement =
    v2Conclusions[0]?.statement ?? trace.summary ?? null;
  const secondaryConclusions = v2Conclusions.slice(1).filter(
    // Conditions and branch_warning render in their own card below.
    (c) =>
      getTraceV2Kind(c) !== "writer_conditions" &&
      getTraceV2Kind(c) !== "branch_warning",
  );

  return (
    <Card>
      <CardHeader
        eyebrow={variant === "assist_secondary" ? "Deterministic" : "Design trace"}
        title={
          variant === "assist_secondary"
            ? "Trace conclusions (source of truth)"
            : "What controls this?"
        }
      />
      <CardBody className="space-y-4">
        {heroStatement ? (
          <p className="text-lg leading-relaxed tracking-tight text-zinc-50">
            {heroStatement}
          </p>
        ) : (
          <p className="text-sm text-zinc-500">
            INTELLI didn&apos;t find a definitive writer for this object.
          </p>
        )}

        {secondaryConclusions.length > 0 ? (
          <ul className="flex flex-col gap-2">
            {secondaryConclusions.map((c, i) => (
              <ConclusionBullet
                key={c.id ?? `${getTraceV2Kind(c) ?? "v1"}-${i}`}
                conclusion={c}
              />
            ))}
          </ul>
        ) : null}

        {variant === "default" && trace.recommended_checks.length > 0 ? (
          <RecommendedChecks checks={trace.recommended_checks} />
        ) : null}
      </CardBody>
    </Card>
  );
}

function ConclusionBullet({ conclusion }: { conclusion: TraceConclusion }) {
  return (
    <li className="flex gap-3 rounded-lg border border-zinc-800/70 bg-zinc-900/40 px-3 py-2">
      <span
        aria-hidden
        className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-zinc-500"
      />
      <div className="min-w-0 flex-1">
        <p className="text-sm leading-snug text-zinc-200">
          {conclusion.statement}
        </p>
        {conclusion.recommended_checks &&
        conclusion.recommended_checks.length > 0 ? (
          <ul className="mt-1 list-disc pl-5 text-xs text-zinc-500">
            {conclusion.recommended_checks.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        ) : null}
      </div>
      {conclusion.confidence ? (
        <ConfidenceBadge value={conclusion.confidence} />
      ) : null}
    </li>
  );
}

function RecommendedChecks({ checks }: { checks: string[] }) {
  return (
    <div className="rounded-lg border border-zinc-800/70 bg-zinc-900/40 px-4 py-3">
      <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
        Recommended checks
      </p>
      <ul className="mt-1 list-disc pl-5 text-sm text-zinc-200">
        {checks.map((c, i) => (
          <li key={i}>{c}</li>
        ))}
      </ul>
    </div>
  );
}

// ===========================================================================
// 3. Conditions card -- aggregates writer_conditions + branch_warning
// natural-language phrasing per writer.
// ===========================================================================

function ConditionsCard({ trace }: { trace: TraceResponse }) {
  const conditionGroups = useMemo(() => {
    const groups: Array<{
      key: string;
      location: string | null;
      instructionType: string | null;
      statement: string;
      conditions: Array<{
        natural_language?: string;
        tag?: string;
        required_value?: boolean | null;
        instruction_type?: string;
      }>;
    }> = [];
    trace.conclusions.forEach((c, i) => {
      if (getTraceV2Kind(c) !== "writer_conditions") return;
      const meta = c.platform_specific ?? {};
      const condsRaw = (meta as { conditions?: unknown }).conditions;
      const conds = Array.isArray(condsRaw)
        ? (condsRaw as Array<{
            natural_language?: string;
            tag?: string;
            required_value?: boolean | null;
            instruction_type?: string;
          }>)
        : [];
      groups.push({
        key: c.id ?? `cond-${i}`,
        location: getStringMeta(meta, "location"),
        instructionType: getStringMeta(meta, "instruction_type"),
        statement: c.statement,
        conditions: conds,
      });
    });
    return groups;
  }, [trace.conclusions]);

  const branchWarnings = useMemo(
    () =>
      trace.conclusions.filter(
        (c) => getTraceV2Kind(c) === "branch_warning",
      ),
    [trace.conclusions],
  );

  if (conditionGroups.length === 0 && branchWarnings.length === 0) {
    return null;
  }

  return (
    <Card>
      <CardHeader
        eyebrow="Key conditions"
        title="When does this fire?"
      />
      <CardBody className="space-y-4">
        {conditionGroups.map((g) => (
          <div key={g.key} className="space-y-2">
            <div className="flex flex-wrap items-baseline gap-2">
              {g.location ? (
                <span className="font-mono text-xs text-zinc-400">
                  {g.location}
                </span>
              ) : null}
              {g.instructionType ? (
                <Badge tone="outline" uppercase>
                  {g.instructionType}
                </Badge>
              ) : null}
            </div>
            <ul className="ml-4 list-disc text-sm text-zinc-200">
              {g.conditions.length > 0
                ? g.conditions.map((c, i) => (
                    <li key={i} className="py-0.5">
                      <span>
                        {c.natural_language ??
                          (c.tag
                            ? `${c.tag} is ${c.required_value ? "TRUE" : "FALSE"}`
                            : "—")}
                      </span>
                    </li>
                  ))
                : null}
            </ul>
            {g.conditions.length === 0 ? (
              <p className="text-sm text-zinc-300">{g.statement}</p>
            ) : null}
          </div>
        ))}

        {branchWarnings.map((c, i) => (
          <div
            key={c.id ?? `branch-${i}`}
            className="rounded-lg border border-amber-900/40 bg-amber-950/20 px-3 py-2"
          >
            <div className="flex items-baseline justify-between gap-2">
              <p className="text-sm text-amber-100">{c.statement}</p>
              <Badge tone="warning" uppercase>
                branches
              </Badge>
            </div>
          </div>
        ))}
      </CardBody>
    </Card>
  );
}

// ===========================================================================
// 4. Counts strip -- compact summary chips between answer and evidence.
// ===========================================================================

function CountsStrip({ trace }: { trace: TraceResponse }) {
  const writers = trace.writer_relationships.length;
  const readers = trace.reader_relationships.length;
  const upstream = trace.upstream_object_ids.length;
  const downstream = trace.downstream_object_ids.length;

  return (
    <div className="flex flex-wrap items-center gap-2 px-1 text-xs text-zinc-400">
      <Stat value={writers} label="writers" />
      <Stat value={readers} label="readers" />
      <Stat value={upstream} label="upstream" />
      <Stat value={downstream} label="downstream" />
    </div>
  );
}

// ===========================================================================
// 5. Evidence accordion -- writers / readers as expandable rows.
// ===========================================================================

function EvidenceAccordion({ trace }: { trace: TraceResponse }) {
  const writers = trace.writer_relationships;
  const readers = trace.reader_relationships;

  if (writers.length === 0 && readers.length === 0) {
    return null;
  }

  return (
    <div className="flex flex-col gap-3">
      {writers.length > 0 ? (
        <Accordion
          eyebrow="Logic graph"
          title="Writers (driving logic)"
          count={writers.length}
        >
          <RelationshipList relationships={writers} />
        </Accordion>
      ) : null}
      {readers.length > 0 ? (
        <Accordion
          eyebrow="Logic graph"
          title="Where this object is read"
          count={readers.length}
        >
          <RelationshipList relationships={readers} />
        </Accordion>
      ) : null}
    </div>
  );
}

function RelationshipList({
  relationships,
}: {
  relationships: TraceRelationship[];
}) {
  return (
    <ul className="flex flex-col gap-2">
      {relationships.map((r, i) => (
        <RelationshipRow
          key={r.id ?? `${r.source_id}->${r.target_id}-${i}`}
          relationship={r}
        />
      ))}
    </ul>
  );
}

function RelationshipRow({
  relationship,
}: {
  relationship: TraceRelationship;
}) {
  const instr = getStringMeta(
    relationship.platform_specific,
    "instruction_type",
  );

  return (
    <li className="rounded-lg border border-zinc-800/70 bg-zinc-900/40 p-3">
      <div className="mb-1 flex flex-wrap items-baseline gap-2">
        <Badge tone="info" uppercase>
          {relationship.relationship_type}
        </Badge>
        {instr ? (
          <Badge tone="outline" uppercase>
            {instr}
          </Badge>
        ) : null}
        {relationship.write_behavior ? (
          <Badge tone="warning" uppercase>
            {relationship.write_behavior}
          </Badge>
        ) : null}
      </div>
      <KVRow k="source" v={relationship.source_id} mono breakAll />
      <KVRow k="target" v={relationship.target_id} mono breakAll />
      {relationship.source_location ? (
        <KVRow
          k="location"
          v={relationship.source_location}
          mono
          breakAll
        />
      ) : null}
      {relationship.logic_condition ? (
        <KVRow
          k="condition"
          v={relationship.logic_condition}
          mono
          breakAll
        />
      ) : null}
      {relationship.platform_specific &&
      Object.keys(relationship.platform_specific).length > 0 ? (
        <details className="mt-2">
          <summary className="cursor-pointer text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-500 hover:text-zinc-300">
            platform_specific
          </summary>
          <div className="mt-1">
            <Code>
              {JSON.stringify(relationship.platform_specific, null, 2)}
            </Code>
          </div>
        </details>
      ) : null}
    </li>
  );
}

// ===========================================================================
// 6. Debug accordion -- raw JSON for the whole response.
// ===========================================================================

function DebugAccordion({ trace }: { trace: TraceResponse }) {
  const v1Conclusions = trace.conclusions.filter(
    (c) => getTraceV2Kind(c) === null,
  );
  return (
    <Accordion eyebrow="Debug" title="Technical evidence">
      <div className="space-y-4">
        {trace.failure_impact.length > 0 ? (
          <div>
            <p className="mb-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
              Failure impact
            </p>
            <ul className="list-disc pl-5 text-sm text-zinc-200">
              {trace.failure_impact.map((c, i) => (
                <li key={i}>{c}</li>
              ))}
            </ul>
          </div>
        ) : null}

        {trace.upstream_object_ids.length > 0 ? (
          <IdsBlock title="Upstream object ids" ids={trace.upstream_object_ids} />
        ) : null}
        {trace.downstream_object_ids.length > 0 ? (
          <IdsBlock
            title="Downstream object ids"
            ids={trace.downstream_object_ids}
          />
        ) : null}

        {v1Conclusions.length > 0 ? (
          <div>
            <p className="mb-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
              Trace v1 conclusions
            </p>
            <ul className="flex flex-col gap-1.5">
              {v1Conclusions.map((c, i) => (
                <li
                  key={c.id ?? `v1-${i}`}
                  className="rounded-md border border-zinc-800/70 bg-zinc-900/40 px-3 py-1.5 text-xs text-zinc-300"
                >
                  {c.statement}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        <div>
          <p className="mb-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
            Raw response
          </p>
          <Code>{JSON.stringify(trace, null, 2)}</Code>
        </div>
      </div>
    </Accordion>
  );
}

function IdsBlock({ title, ids }: { title: string; ids: string[] }) {
  return (
    <div>
      <p className="mb-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
        {title}
      </p>
      <ul className="flex flex-col gap-1">
        {ids.map((id) => (
          <li
            key={id}
            className="break-all rounded border border-zinc-800 bg-zinc-900/60 px-2 py-1 font-mono text-[11px] text-zinc-300"
          >
            {id}
          </li>
        ))}
      </ul>
    </div>
  );
}
