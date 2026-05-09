/** Mirrors backend `app.models.control_model` (subset used by the UI). */

export interface ControlTag {
  name: string;
  data_type?: string | null;
  scope?: string | null;
}

export interface ControlInstruction {
  instruction_type: string;
  operands: string[];
  raw_text?: string | null;
  output?: string | null;
  rung_number?: number | null;
  id?: string | null;
}

export interface ControlRoutine {
  name: string;
  language?: string | null;
  instructions: ControlInstruction[];
  raw_logic?: string | null;
}

export interface ControlProgram {
  name: string;
  tags: ControlTag[];
  routines: ControlRoutine[];
}

export interface ControlController {
  name: string;
  controller_tags: ControlTag[];
  programs: ControlProgram[];
}

export interface ControlProject {
  project_name: string;
  controllers: ControlController[];
}

export interface TraceCause {
  tag: string;
  relationship: string;
  instruction_type?: string | null;
  routine?: string | null;
  program?: string | null;
  raw_text?: string | null;
}

export interface TraceResult {
  target_tag: string;
  question: string;
  status?: string | null;
  summary: string;
  causes: TraceCause[];
}

export interface ExplanationResult {
  target_tag: string;
  explanation: string;
  trace: TraceResult;
}

export interface UploadPayload {
  project_id: string;
  connector: string;
  project: ControlProject;
  graph: Record<string, number>;
}
