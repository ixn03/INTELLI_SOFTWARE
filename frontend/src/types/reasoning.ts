/**
 * TypeScript mirrors of the backend reasoning schema
 * (`backend/app/models/reasoning.py`) -- only the fields the
 * INTELLI frontend currently consumes.
 *
 * The backend serializes string-enums as their string value, so all
 * enum-typed fields are typed as string unions (or `string` where the
 * surface is too wide to enumerate usefully -- e.g.
 * ``relationship_type``).
 *
 * Endpoints currently consumed:
 *   - GET  /api/normalized-summary  -> NormalizedSummaryResponse
 *   - GET  /api/control-objects       -> ControlObjectsPageResponse
 *   - GET  /api/sequence-summary      -> (dict, sequence reasoning v1)
 *   - POST /api/trace-v1            -> TraceResponse
 *   - POST /api/trace-v2            -> TraceResponse (NL-augmented)
 *   - POST /api/ask-v1              -> AskResponse  (router metadata)
 *   - POST /api/ask-v2              -> AskResponse  (orchestration + optional runtime)
 *   - POST /api/ask-v3              -> LLMAssistResponse (deterministic-first assist)
 *   - POST /api/evaluate-runtime-v2 -> TraceResponse (runtime v2 overlay)
 */

export type ConfidenceLevel =
  | "very_low"
  | "low"
  | "medium"
  | "high"
  | "very_high"
  | "unknown";

// ---------------------------------------------------------------------------
// GET /api/normalized-summary
// ---------------------------------------------------------------------------

export interface NormalizedControlObjectSummary {
  id: string;
  name: string | null;
  object_type: string;
  source_location: string | null;
}

export interface NormalizedRelationshipSummary {
  source_id: string;
  target_id: string;
  relationship_type: string;
  source_location: string | null;
  platform_specific: Record<string, unknown>;
}

export interface NormalizedSummaryResponse {
  project_id: string | null;
  /** Total objects in project (unfiltered). */
  control_object_count: number;
  /** Objects matching current search / type filter. */
  total_control_object_count: number;
  returned_control_object_count: number;
  offset: number;
  limit: number;
  relationship_count: number;
  total_relationship_count: number;
  returned_relationship_count: number;
  rel_offset: number;
  rel_limit: number;
  execution_context_count: number;
  control_objects: NormalizedControlObjectSummary[];
  relationships: NormalizedRelationshipSummary[];
}

/** GET /api/control-objects */
export interface ControlObjectsPageResponse {
  project_id: string;
  project_control_object_count: number;
  total_control_object_count: number;
  returned_control_object_count: number;
  offset: number;
  limit: number;
  control_objects: NormalizedControlObjectSummary[];
}

// ---------------------------------------------------------------------------
// Trace v1 / v2 shared shape
// ---------------------------------------------------------------------------

/**
 * Subset of the backend ``Relationship`` model surfaced in the UI.
 * The backend may include additional fields (evidence, conflict
 * flags, timing_behavior, ...) -- we leave them off the type until
 * the UI actually renders them.
 */
export interface TraceRelationship {
  id?: string;
  source_id: string;
  target_id: string;
  relationship_type: string;
  source_location?: string | null;
  logic_condition?: string | null;
  write_behavior?: string | null;
  execution_context_id?: string | null;
  platform_specific?: Record<string, unknown>;
}

/**
 * Subset of the backend ``TruthConclusion`` model surfaced in the
 * UI. ``platform_specific.trace_v2_kind`` is the most useful tag
 * for rendering: it distinguishes Trace v2's natural-language
 * conclusions (``writer_what`` / ``writer_conditions`` /
 * ``st_assignment`` / ``st_too_complex`` / ``branch_warning``)
 * from legacy v1 conclusions (no ``trace_v2_kind``).
 */
export interface TraceConclusion {
  id?: string;
  statement: string;
  subject_ids?: string[];
  recommended_checks?: string[];
  confidence?: ConfidenceLevel;
  platform_specific?: Record<string, unknown>;
}

export interface TraceResponse {
  target_object_id: string;
  summary?: string | null;
  confidence?: ConfidenceLevel;
  upstream_object_ids: string[];
  downstream_object_ids: string[];
  writer_relationships: TraceRelationship[];
  reader_relationships: TraceRelationship[];
  relationships: TraceRelationship[];
  conclusions: TraceConclusion[];
  recommended_checks: string[];
  failure_impact: string[];
  /**
   * Trace v2 sets ``platform_specific.trace_version`` to ``"v2"`` and
   * surfaces ``natural_conclusion_count``. Ask v1 surfaces the
   * router metadata (``question``, ``detected_target_object_id``,
   * ``detected_intent``, ``router_version``).
   */
  platform_specific?: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Evidence Engine / Trustworthiness v1
// ---------------------------------------------------------------------------

export interface EvidenceItem {
  id: string;
  evidence_type: string;
  source_platform?: string | null;
  source_location?: string | null;
  target_object_id?: string | null;
  statement: string;
  confidence: number;
  deterministic: boolean;
  related_relationship_ids: string[];
  runtime_snapshot_keys: string[];
  unsupported: boolean;
  metadata: Record<string, unknown>;
}

export interface EvidenceBundle {
  conclusion: string;
  supporting_evidence: EvidenceItem[];
  conflicting_evidence: EvidenceItem[];
  unsupported_evidence: EvidenceItem[];
  confidence: number;
  warnings: string[];
}

export interface TrustAssessment {
  confidence_score: number;
  uncertainty_reasons: string[];
  unsupported_reasons: string[];
  conflicting_reasons: string[];
  missing_runtime_reasons: string[];
  parser_coverage_reasons: string[];
  recommendation_level: string;
}

export function asRecord(v: unknown): Record<string, unknown> | null {
  return v !== null && typeof v === "object" && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : null;
}

function asStringArray(v: unknown): string[] {
  if (!Array.isArray(v)) return [];
  return v.filter((x): x is string => typeof x === "string");
}

function asEvidenceItemArray(v: unknown): EvidenceItem[] {
  if (!Array.isArray(v)) return [];
  return v
    .map((raw): EvidenceItem | null => {
      const o = asRecord(raw);
      if (!o || typeof o.statement !== "string") return null;
      return {
        id: typeof o.id === "string" ? o.id : "",
        evidence_type:
          typeof o.evidence_type === "string" ? o.evidence_type : "unknown",
        source_platform:
          typeof o.source_platform === "string" ? o.source_platform : null,
        source_location:
          typeof o.source_location === "string" ? o.source_location : null,
        target_object_id:
          typeof o.target_object_id === "string" ? o.target_object_id : null,
        statement: o.statement,
        confidence: typeof o.confidence === "number" ? o.confidence : 0.5,
        deterministic: Boolean(o.deterministic),
        related_relationship_ids: asStringArray(o.related_relationship_ids),
        runtime_snapshot_keys: asStringArray(o.runtime_snapshot_keys),
        unsupported: Boolean(o.unsupported),
        metadata: asRecord(o.metadata) ?? {},
      };
    })
    .filter((x): x is EvidenceItem => x !== null);
}

export function parseEvidenceBundle(raw: unknown): EvidenceBundle | null {
  const o = asRecord(raw);
  if (!o || typeof o.conclusion !== "string") return null;
  return {
    conclusion: o.conclusion,
    supporting_evidence: asEvidenceItemArray(o.supporting_evidence),
    conflicting_evidence: asEvidenceItemArray(o.conflicting_evidence),
    unsupported_evidence: asEvidenceItemArray(o.unsupported_evidence),
    confidence: typeof o.confidence === "number" ? o.confidence : 0.5,
    warnings: asStringArray(o.warnings),
  };
}

export function parseTrustAssessment(raw: unknown): TrustAssessment | null {
  const o = asRecord(raw);
  if (!o || typeof o.confidence_score !== "number") return null;
  return {
    confidence_score: o.confidence_score,
    uncertainty_reasons: asStringArray(o.uncertainty_reasons),
    unsupported_reasons: asStringArray(o.unsupported_reasons),
    conflicting_reasons: asStringArray(o.conflicting_reasons),
    missing_runtime_reasons: asStringArray(o.missing_runtime_reasons),
    parser_coverage_reasons: asStringArray(o.parser_coverage_reasons),
    recommendation_level:
      typeof o.recommendation_level === "string"
        ? o.recommendation_level
        : "review",
  };
}

export function evidenceBundleFromTrace(trace: TraceResponse): EvidenceBundle | null {
  const ps = trace.platform_specific;
  return (
    parseEvidenceBundle(ps?.["runtime_evidence_bundle"]) ??
    parseEvidenceBundle(ps?.["evidence_bundle"]) ??
    null
  );
}

export function trustFromTrace(trace: TraceResponse): TrustAssessment | null {
  return parseTrustAssessment(trace.platform_specific?.["trust_assessment"]);
}

/**
 * Back-compatible alias. The two trace endpoints return the same
 * shape; the legacy name is kept so existing imports don't break.
 *
 * @deprecated Prefer ``TraceResponse``.
 */
export type TraceV1Response = TraceResponse;

// ---------------------------------------------------------------------------
// POST /api/ask-v1
// ---------------------------------------------------------------------------

/**
 * Router-level fields surfaced in the response's
 * ``platform_specific``. The ``answer_question`` service in the
 * backend always sets these even when no target is identified.
 */
export interface AskRouterMetadata {
  question?: string;
  detected_target_object_id?: string | null;
  detected_intent?: string;
  router_version?: string;
}

export type AskResponse = TraceResponse;

// ---------------------------------------------------------------------------
// Render-time helpers used by the UI layer. Kept here so the trace
// component(s) can pattern-match on them without re-deriving the
// string literals.
// ---------------------------------------------------------------------------

export type TraceV2Kind =
  | "writer_what"
  | "writer_conditions"
  | "st_assignment"
  | "st_too_complex"
  | "branch_warning"
  /** Prepended by ``/api/evaluate-runtime-v2`` — not design-time Trace v2. */
  | "runtime_v2_verdict"
  | "runtime_v2_path";

export function getTraceV2Kind(
  conclusion: TraceConclusion,
): TraceV2Kind | null {
  const k = conclusion.platform_specific?.["trace_v2_kind"];
  return typeof k === "string" ? (k as TraceV2Kind) : null;
}

// ---------------------------------------------------------------------------
// POST /api/evaluate-runtime-v2  (platform_specific on TraceResponse)
// ---------------------------------------------------------------------------

/** Overall operational verdict from ``runtime_evaluation_v2_service``. */
export type OverallVerdict =
  | "target_can_be_on"
  | "target_likely_off_or_reset"
  | "conflict_or_scan_order_dependent"
  | "blocked"
  | "incomplete";

/** Per-writer path status. */
export type PathStatus =
  | "path_satisfied"
  | "path_blocked"
  | "path_incomplete"
  | "path_unsupported";

/** One evaluated condition row in ``platform_specific.*_conditions``. */
export interface RuntimeConditionResult {
  status: string;
  natural_language?: string;
  instruction_type?: string | null;
  or_branch_index?: number;
  tag?: string | null;
  member?: string | null;
  snapshot_key?: string | null;
  required_value?: unknown;
  actual_value?: unknown;
  comparison_operator?: string | null;
  compared_with?: unknown;
  reason?: string;
  location?: string;
  writer_instruction_type?: string | null;
}

export interface RuntimeWriterPathResult {
  status: PathStatus | string;
  write_effect: string;
  location: string;
  instruction_type?: string | null;
  target_id?: string | null;
  source_id?: string | null;
  assigned_value?: string | null;
  conditions: RuntimeConditionResult[];
}

export interface RuntimeConflict {
  kind?: string;
  true_writer?: { location?: string; instruction_type?: string | null };
  false_writer?: { location?: string; instruction_type?: string | null };
}

/**
 * Typed view of ``TraceResponse.platform_specific`` after a successful
 * ``POST /api/evaluate-runtime-v2``. The backend still returns a loose
 * ``Record<string, unknown>`` on ``TraceResponse``; use
 * ``parseRuntimeV2PlatformSpecific`` for safe access.
 */
export interface RuntimeV2PlatformSpecific {
  trace_version: "runtime_v2";
  runtime_snapshot_evaluated: boolean;
  overall_verdict: OverallVerdict;
  writer_path_results: RuntimeWriterPathResult[];
  blocking_conditions: RuntimeConditionResult[];
  satisfied_conditions: RuntimeConditionResult[];
  missing_conditions: RuntimeConditionResult[];
  unsupported_conditions: RuntimeConditionResult[];
  conflicts: RuntimeConflict[];
  runtime_evidence_bundle?: EvidenceBundle | null;
  trust_assessment?: TrustAssessment | null;
}

function isOverallVerdict(v: unknown): v is OverallVerdict {
  return (
    v === "target_can_be_on" ||
    v === "target_likely_off_or_reset" ||
    v === "conflict_or_scan_order_dependent" ||
    v === "blocked" ||
    v === "incomplete"
  );
}

function asConditionArray(v: unknown): RuntimeConditionResult[] {
  if (!Array.isArray(v)) return [];
  return v.filter((x) => x !== null && typeof x === "object") as RuntimeConditionResult[];
}

function asWriterPathArray(v: unknown): RuntimeWriterPathResult[] {
  if (!Array.isArray(v)) return [];
  return v
    .filter((x) => x !== null && typeof x === "object")
    .map((raw) => {
      const o = raw as Record<string, unknown>;
      return {
        status: typeof o.status === "string" ? o.status : "path_unsupported",
        write_effect: typeof o.write_effect === "string" ? o.write_effect : "other",
        location: typeof o.location === "string" ? o.location : "",
        instruction_type:
          typeof o.instruction_type === "string" ? o.instruction_type : null,
        target_id: typeof o.target_id === "string" ? o.target_id : null,
        source_id: typeof o.source_id === "string" ? o.source_id : null,
        assigned_value:
          typeof o.assigned_value === "string" ? o.assigned_value : null,
        conditions: asConditionArray(o.conditions),
      };
    });
}

function asConflictArray(v: unknown): RuntimeConflict[] {
  if (!Array.isArray(v)) return [];
  return v.filter((x) => x !== null && typeof x === "object") as RuntimeConflict[];
}

/**
 * Returns parsed runtime v2 metadata when ``trace_version`` is
 * ``runtime_v2``, otherwise ``null``.
 */
export function parseRuntimeV2PlatformSpecific(
  platformSpecific: Record<string, unknown> | undefined,
): RuntimeV2PlatformSpecific | null {
  if (!platformSpecific) return null;
  if (platformSpecific["trace_version"] !== "runtime_v2") return null;
  const ov = platformSpecific["overall_verdict"];
  if (!isOverallVerdict(ov)) return null;
  return {
    trace_version: "runtime_v2",
    runtime_snapshot_evaluated: Boolean(
      platformSpecific["runtime_snapshot_evaluated"],
    ),
    overall_verdict: ov,
    writer_path_results: asWriterPathArray(
      platformSpecific["writer_path_results"],
    ),
    blocking_conditions: asConditionArray(
      platformSpecific["blocking_conditions"],
    ),
    satisfied_conditions: asConditionArray(
      platformSpecific["satisfied_conditions"],
    ),
    missing_conditions: asConditionArray(
      platformSpecific["missing_conditions"],
    ),
    unsupported_conditions: asConditionArray(
      platformSpecific["unsupported_conditions"],
    ),
    conflicts: asConflictArray(platformSpecific["conflicts"]),
    runtime_evidence_bundle: parseEvidenceBundle(
      platformSpecific["runtime_evidence_bundle"],
    ),
    trust_assessment: parseTrustAssessment(platformSpecific["trust_assessment"]),
  };
}

/** True when the trace payload carries a runtime v2 evaluation overlay. */
export function isRuntimeV2Trace(trace: TraceResponse): boolean {
  return parseRuntimeV2PlatformSpecific(trace.platform_specific) !== null;
}

/** First conclusion with ``trace_v2_kind === "runtime_v2_verdict"``. */
export function getRuntimeV2VerdictConclusion(
  trace: TraceResponse,
): TraceConclusion | null {
  return (
    trace.conclusions.find(
      (c) => c.platform_specific?.["trace_v2_kind"] === "runtime_v2_verdict",
    ) ?? null
  );
}

const RUNTIME_V2_CONCLUSION_KINDS = new Set<string>([
  "runtime_v2_verdict",
  "runtime_v2_path",
]);

/** Design-time Trace v2 / v1 conclusions only (excludes runtime overlay rows). */
export function isDesignTraceConclusion(conclusion: TraceConclusion): boolean {
  const k = conclusion.platform_specific?.["trace_v2_kind"];
  if (typeof k === "string" && RUNTIME_V2_CONCLUSION_KINDS.has(k)) {
    return false;
  }
  return true;
}

export function getStringMeta(
  source: Record<string, unknown> | undefined,
  key: string,
): string | null {
  if (!source) return null;
  const v = source[key];
  return typeof v === "string" ? v : null;
}

// ---------------------------------------------------------------------------
// POST /api/ask-v3  (LLM Assist v1 — deterministic-first)
// ---------------------------------------------------------------------------

/** Response from ``POST /api/ask-v3``; includes full deterministic trace. */
export type AskAnswerStyle =
  | "concise_operator"
  | "controls_engineer"
  | "detailed_reasoning";

export interface AskV3ConversationContext {
  current_selected_object?: string | null;
  last_discussed_state?: string | null;
  prior_runtime_snapshot_present?: boolean;
  prior_sequence_discussion?: Record<string, unknown>;
}

export interface LLMAssistResponse {
  answer: string;
  confidence: string;
  target_object_id: string | null;
  detected_intent: string;
  evidence_used: Record<string, unknown>;
  warnings: string[];
  deterministic_result: TraceResponse;
}

export interface SequenceSemanticSummary {
  current_possible_states: Array<Record<string, unknown>>;
  likely_waiting_conditions: Array<Record<string, unknown>>;
  transition_conditions: Array<Record<string, unknown>>;
  fault_conditions: Array<Record<string, unknown>>;
  manual_override_conditions: Array<Record<string, unknown>>;
  confidence: number;
  unsupported_patterns: Array<Record<string, unknown>>;
}

export interface VersionImpactSummary {
  operationally_significant_changes: string[];
  possible_runtime_impacts: string[];
  affected_equipment: string[];
  affected_sequences: string[];
  changed_states: string[];
  changed_fault_behavior: string[];
  risk_level: string;
  confidence: number;
  evidence: Record<string, unknown>;
}
