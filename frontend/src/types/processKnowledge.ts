export type RecordType =
  | "control_narrative"
  | "io_list"
  | "cause_effect_matrix"
  | "alarm_rationalization"
  | "moc"
  | "knowledge_issue"
  | "engineering_note";

export type RecordStatus = "active" | "needing_setup" | "needs_review" | "archived";
export type RevisionStatus = "draft" | "proposed" | "approved" | "rejected" | "archived";
export type ReviewStatus =
  | "open"
  | "approved"
  | "rejected"
  | "needs_manual_review"
  | "resolved";

export interface ProcessUnit {
  id: string;
  name: string;
  area?: string | null;
  description?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProcessUnitListResponse {
  items: ProcessUnit[];
  total: number;
}

export interface EquipmentModule {
  id: string;
  process_unit_id: string;
  name: string;
  module_type?: string | null;
  description?: string | null;
  aliases: string[];
  metadata_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ControlImportSource {
  id: string;
  process_unit_id: string;
  module_id?: string | null;
  name: string;
  source_system: string;
  acquisition_mode: string;
  connector_hint?: string | null;
  endpoint_url?: string | null;
  export_path?: string | null;
  schedule?: string | null;
  is_enabled: boolean;
  config_json: Record<string, unknown>;
  last_run_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface LogicSnapshot {
  id: string;
  module_id: string;
  source_type: string;
  source_filename: string;
  file_hash?: string | null;
  export_timestamp?: string | null;
  parsed_extract: Record<string, unknown>;
  raw_project_id?: string | null;
  notes?: string | null;
  created_by?: string | null;
  created_at: string;
}

export interface EngineeringRecord {
  id: string;
  process_unit_id: string;
  module_id?: string | null;
  record_type: RecordType;
  title: string;
  status: RecordStatus;
  owner?: string | null;
  description?: string | null;
  metadata_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface DocumentRevision {
  id: string;
  engineering_record_id: string;
  source_logic_snapshot_id?: string | null;
  template_id?: string | null;
  revision: string;
  status: RevisionStatus;
  body_markdown?: string | null;
  structured_content: Record<string, unknown>;
  created_by?: string | null;
  approved_by?: string | null;
  approved_at?: string | null;
  created_at: string;
}

export interface LogicDiff {
  id: string;
  module_id: string;
  previous_snapshot_id: string;
  current_snapshot_id: string;
  changed: boolean;
  changed_routines: Record<string, unknown>[];
  changed_tags: Record<string, unknown>[];
  changed_reads_writes: Record<string, unknown>[];
  summary_payload: Record<string, unknown>;
  impacted_record_ids: string[];
  created_at: string;
}

export interface ProposedDocumentUpdate {
  id: string;
  engineering_record_id: string;
  source_logic_snapshot_id: string;
  source_logic_diff_id?: string | null;
  base_document_revision_id?: string | null;
  proposed_body?: string | null;
  proposed_structured_changes: Record<string, unknown>;
  confidence?: string | null;
  status: string;
  created_at: string;
}

export interface ReviewItem {
  id: string;
  process_unit_id: string;
  module_id?: string | null;
  engineering_record_id?: string | null;
  logic_snapshot_id?: string | null;
  logic_diff_id?: string | null;
  proposed_document_update_id?: string | null;
  title: string;
  reason?: string | null;
  status: ReviewStatus;
  assigned_to?: string | null;
  created_at: string;
  resolved_at?: string | null;
}

export interface ApprovalHistory {
  id: string;
  review_item_id?: string | null;
  engineering_record_id?: string | null;
  document_revision_id?: string | null;
  proposed_document_update_id?: string | null;
  decision: "approved" | "rejected" | "commented";
  actor: string;
  comments?: string | null;
  created_at: string;
}

export interface ImportSyncResult {
  run: {
    id: string;
    source_id: string;
    module_id?: string | null;
    status: string;
    acquired_filename?: string | null;
    project_id?: string | null;
    previous_project_id?: string | null;
    snapshot_id?: string | null;
    diff_summary?: string | null;
    impacted_records: Record<string, unknown>[];
    error_message?: string | null;
    started_at: string;
    completed_at?: string | null;
  };
  snapshot?: LogicSnapshot | null;
  diff?: Record<string, unknown> | null;
  logic_diff?: LogicDiff | null;
  impacted_records: Record<string, unknown>[];
  process_unit_id?: string | null;
  module_id?: string | null;
  import_source_id?: string | null;
  snapshot_id?: string | null;
  diff_id?: string | null;
  changed: boolean;
  review_item_ids: string[];
  affected_record_ids: string[];
}

export interface ModuleRecord {
  module: EquipmentModule;
  snapshots: LogicSnapshot[];
  narratives: unknown[];
  governance_artifacts: unknown[];
  issues: unknown[];
}
