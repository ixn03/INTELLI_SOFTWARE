/**
 * TypeScript mirrors of the backend reasoning schema
 * (`backend/app/models/reasoning.py`) -- only the fields the
 * Reasoning Trace v1 debug panel currently consumes.
 *
 * Backend serializes string-enums as their string value, so all
 * enum-typed fields are typed as string unions (or `string` where
 * the surface is too wide to enumerate usefully -- e.g.
 * `relationship_type`).
 *
 * Endpoints:
 *   - GET  /api/normalized-summary   -> NormalizedSummaryResponse
 *   - POST /api/trace-v1             -> TraceV1Response
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
  control_object_count: number;
  relationship_count: number;
  execution_context_count: number;
  control_objects: NormalizedControlObjectSummary[];
  relationships: NormalizedRelationshipSummary[];
}

// ---------------------------------------------------------------------------
// POST /api/trace-v1
// ---------------------------------------------------------------------------

/**
 * Subset of the backend `Relationship` model surfaced in the debug
 * panel. Backend may include additional fields (evidence, conflict
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
 * Subset of the backend `TruthConclusion` model surfaced in the panel.
 */
export interface TraceConclusion {
  id?: string;
  statement: string;
  subject_ids?: string[];
  recommended_checks?: string[];
  confidence?: ConfidenceLevel;
}

export interface TraceV1Response {
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
}
