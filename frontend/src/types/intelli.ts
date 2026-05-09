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

export interface ControlProgram {
  name: string;
  routines: ControlRoutine[];
}

export interface ControlController {
  name: string;
  programs: ControlProgram[];
}

export interface ControlProject {
  project_name: string;
  controllers: ControlController[];
}
