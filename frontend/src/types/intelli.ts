export interface ControlInstruction {
  id?: string;
  instruction_type: string;
  operands: string[];
  output?: string;
  raw_text?: string;
  language?: string;
}

export interface ControlRoutine {
  name: string;
  language: string;
  raw_logic?: string;
  instructions: ControlInstruction[];
}

export interface ControlTag {
  name: string;
}

export interface ControlProgram {
  name: string;
  tags?: ControlTag[];
  routines: ControlRoutine[];
}

export interface ControlController {
  name: string;
  controller_tags?: ControlTag[];
  programs: ControlProgram[];
}

export interface ControlProject {
  project_name: string;
  /** Rockwell L5X store key; may mirror ``project_id`` from upload. */
  file_hash?: string | null;
  controllers: ControlController[];
}
