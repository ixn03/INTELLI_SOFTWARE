import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { SignalTroubleshootingWorkspace } from "@/types/reasoning";

import { SignalTroubleshootingWorkspaceView } from "./SignalTroubleshootingWorkspaceView";

const unifiedWorkspace: SignalTroubleshootingWorkspace = {
  question: "Why is Output_B not energizing?",
  interpretation: {
    intent: "why_not_energized",
    target_signal_candidates: [],
    selected_target_signal: {
      id: "tag::Synth/PRG_A/Output_B",
      name: "Output_B",
      source_location: "Controller:Synth/Program:PRG_A/Tag:Output_B",
      match_type: "exact_tag_match",
      score: 0.99,
    },
    confidence: 0.9,
    metadata: {},
  },
  target_signal: {
    id: "tag::Synth/PRG_A/Output_B",
    name: "Output_B",
    object_type: "tag",
    source_location: "Controller:Synth/Program:PRG_A/Tag:Output_B",
  },
  unified_evidence: {
    target_signal_id: "tag::Synth/PRG_A/Output_B",
    target_signal_name: "Output_B",
    summary: {
      answer:
        "Output_B is controlled by 1 upstream condition and written in 2 locations.",
      confidence: "high",
    },
    what_controls_this_signal: [
      {
        signal_id: "tag::Synth/PRG_A/Permissive_A",
        signal_name: "Permissive_A",
        required_for_writer_ids: ["rel::write::1"],
        source_provenance: {
          originating_language: "ladder",
          originating_platform: "rockwell",
          source_location:
            "Controller:Synth/Program:PRG_A/Routine:Routine_A/Rung[1]",
          routine: "Routine_A",
          rung_number: 1,
          block_id: null,
          block_name: null,
          block_type: null,
          pin_name: null,
          pin_direction: null,
          statement_index: null,
          instruction_type: "XIC",
          relationship_ids: ["rel::read::1"],
          source_object_id: "rung::1",
          causality: "deterministic",
          metadata: {},
        },
        confidence: 0.92,
        causality: "deterministic",
      },
    ],
    who_writes_this_signal: [
      {
        writer_type: "ladder_rung",
        signal_id: "tag::Synth/PRG_A/Output_B",
        signal_name: "Output_B",
        source_provenance: {
          originating_language: "ladder",
          originating_platform: "rockwell",
          source_location:
            "Controller:Synth/Program:PRG_A/Routine:Routine_A/Rung[2]",
          routine: "Routine_A",
          rung_number: 2,
          block_id: null,
          block_name: null,
          block_type: null,
          pin_name: null,
          pin_direction: null,
          statement_index: null,
          instruction_type: "OTE",
          relationship_ids: ["rel::write::ladder"],
          source_object_id: "rung::2",
          causality: "deterministic",
          metadata: {},
        },
        confidence: 0.92,
        causality: "deterministic",
        condition_signal_ids: [],
        condition_signal_names: [],
        write_behavior: "sets_true",
      },
      {
        writer_type: "aoi_parameter",
        signal_id: "tag::Synth/PRG_A/Output_B",
        signal_name: "Output_B",
        source_provenance: {
          originating_language: "aoi",
          originating_platform: "rockwell",
          source_location:
            "Controller:Synth/Program:PRG_A/Routine:FBD_Main/Block:Calc_A",
          routine: "FBD_Main",
          rung_number: null,
          block_id: "Calc_A",
          block_name: "AMP_ABSOLUTE_MOVE_INTRALOX",
          block_type: "vendor_amp_block",
          pin_name: "Out",
          pin_direction: "Output",
          statement_index: null,
          instruction_type: null,
          relationship_ids: ["rel::write::aoi"],
          source_object_id: "block::1",
          causality: "deterministic",
          metadata: { parameter_name: "Out" },
        },
        confidence: 0.92,
        causality: "deterministic",
        condition_signal_ids: [],
        condition_signal_names: [],
        write_behavior: "moves_value",
      },
    ],
    where_is_it_used: [],
    upstream_dependencies: [],
    downstream_impact: [],
    unknowns: [
      {
        signal_id: "tag::Synth/PRG_A/Output_B",
        signal_name: "Output_B",
        reference_source_id: "block::generic",
        reference_source_name: "Generic_Block",
        source_provenance: {
          originating_language: "unknown",
          originating_platform: "rockwell",
          source_location: "Controller:Synth/Program:PRG_A/Routine:Routine_A/Block:9",
          routine: "Routine_A",
          rung_number: null,
          block_id: "9",
          block_name: "Generic_Block",
          block_type: null,
          pin_name: null,
          pin_direction: null,
          statement_index: null,
          instruction_type: null,
          relationship_ids: ["rel::ref::1"],
          source_object_id: "block::generic",
          causality: "direction_unknown",
          metadata: {},
        },
        message:
          "Referenced by unknown-direction block. INTELLI preserved the relationship but did not infer causality.",
        confidence: 0.48,
      },
    ],
    evidence_sources: {
      ladder: 2,
      fbd: 1,
      structured_text: 0,
      sfc: 0,
      aoi: 1,
      unknown: 1,
    },
    confidence_summary: {
      confidence: 0.82,
      confidence_label: "high",
      evidence: ["parsed_plc_logic", "deterministic_writer"],
      missing_evidence: ["live_tag_values", "engineer_confirmation"],
      warnings: ["Multiple writers found; review scan order and last-writer behavior."],
    },
    verification: {
      ladder: [
        {
          routine: "Routine_A",
          rung_number: 2,
          instruction_type: "OTE",
          source_location:
            "Controller:Synth/Program:PRG_A/Routine:Routine_A/Rung[2]",
          role: "writer",
          signal_name: "Output_B",
          condition_signal_names: [],
          relationship_ids: ["rel::write::ladder"],
          confidence: 0.92,
        },
      ],
      fbd: [
        {
          routine: "FBD_Main",
          block_name: "Calc_A",
          block_type: "CALC",
          pin_name: "Out",
          pin_direction: "output",
          role: "output",
          signal_name: "Output_B",
          connected_signal_name: null,
          source_location:
            "Controller:Synth/Program:PRG_A/Routine:FBD_Main/Block:Calc_A",
          relationship_ids: ["rel::write::fbd"],
          confidence: 0.9,
          structural_only: false,
        },
      ],
      aoi: [
        {
          aoi_instance: "AmpInst_1",
          parameter_name: "Out",
          parameter_direction: "Output",
          signal_name: "Output_B",
          source_location:
            "Controller:Synth/Program:PRG_A/Routine:Routine_A/Rung[4]",
          relationship_ids: ["rel::write::aoi"],
          confidence: 0.92,
        },
      ],
      structured_text: [],
      sfc: [],
      unknown: [
        {
          reference_source: "Generic_Block",
          signal_name: "Output_B",
          source_location: "Controller:Synth/Program:PRG_A/Routine:Routine_A/Block:9",
          message:
            "Referenced by unknown-direction block. INTELLI preserved the relationship but did not infer causality.",
          relationship_ids: ["rel::ref::1"],
          confidence: 0.48,
        },
      ],
    },
    advanced_details: {
      relationship_ids: ["rel::write::ladder", "rel::write::aoi", "rel::ref::1"],
      dependency_edge_count: 2,
    },
  },
  what_controls_this_signal: {
    upstream_required_conditions: [
      {
        source_id: "rung::1",
        source_name: "Rung 1",
        source_type: "rung",
        target_id: "tag::Synth/PRG_A/Permissive_A",
        target_name: "Permissive_A",
        relationship_type: "reads",
        source_location:
          "Controller:Synth/Program:PRG_A/Routine:Routine_A/Rung[1]",
        instruction_type: "XIC",
        write_behavior: null,
        condition_signal_ids: [],
        condition_signal_names: [],
        confidence: 0.92,
        metadata: {
          relationship_id: "rel::read::1",
          writer_relationship_id: "rel::write::ladder",
          operand_semantic_role: "boolean_condition_read",
        },
      },
    ],
    upstream_dependencies: [],
    writer_conditions: [],
    logic_paths: [
      {
        id: "path::rung::2::tag::Synth/PRG_A/Output_B",
        source_location:
          "Controller:Synth/Program:PRG_A/Routine:Routine_A/Rung[2]",
        routine: "Routine_A",
        rung_number: 2,
        statement_index: null,
        block_name: null,
        language: "ladder",
        instruction_sequence: ["OTE"],
        readable_expression: "IF Permissive_A THEN Output_B = TRUE",
        input_signals: [
          {
            signal_id: "tag::Synth/PRG_A/Permissive_A",
            signal_name: "Permissive_A",
            instruction_type: "XIC",
            relationship_id: "rel::read::1",
          },
        ],
        output_signals: [
          {
            signal_id: "tag::Synth/PRG_A/Output_B",
            signal_name: "Output_B",
            instruction_type: "OTE",
            relationship_id: "rel::write::ladder",
          },
        ],
        write_operations: [
          {
            signal_id: "tag::Synth/PRG_A/Output_B",
            signal_name: "Output_B",
            instruction_type: "OTE",
            write_behavior: "sets_true",
            relationship_id: "rel::write::ladder",
          },
        ],
        confidence: 0.92,
        warnings: [],
        metadata: { path_kind: "boolean_control" },
      },
    ],
    data_source_reads: [],
    write_operations: [],
    derived_calculations: [],
    derived_explanation: null,
    unknown_direction_references: [
      {
        source_id: "block::generic",
        source_name: "Generic_Block",
        source_type: "function_block",
        target_id: "tag::Synth/PRG_A/Output_B",
        target_name: "Output_B",
        relationship_type: "references",
        source_location:
          "Controller:Synth/Program:PRG_A/Routine:Routine_A/Block:9",
        instruction_type: null,
        write_behavior: null,
        condition_signal_ids: [],
        condition_signal_names: [],
        confidence: 0.48,
        metadata: { binding_status: "direction_unknown" },
      },
    ],
  },
  writer_rungs: [],
  upstream_required_conditions: [],
  downstream_readers: [],
  unknown_direction_blocks: [],
  confidence_summary: {
    confidence: 0.82,
    evidence: [],
    missing_evidence: [],
    warnings: [],
  },
  deterministic_explanation:
    "Output_B is controlled by 1 upstream condition and written in 2 locations.",
  advanced_details: {},
};

const calculationWorkspace: SignalTroubleshootingWorkspace = {
  ...unifiedWorkspace,
  question: "How is MaxRecipeNum calculated?",
  target_signal: {
    id: "tag::Synth/PRG_A/MaxRecipeNum",
    name: "MaxRecipeNum",
    object_type: "tag",
    source_location: "Controller:Synth/Program:PRG_A/Tag:MaxRecipeNum",
  },
  unified_evidence: null,
  deterministic_explanation:
    "MaxRecipeNum is calculated from the size of Internal_Recipes[0], then decremented by 1.",
  what_controls_this_signal: {
    upstream_required_conditions: [],
    upstream_dependencies: [],
    writer_conditions: [],
    logic_paths: [
      {
        id: "path::rung::calc/10",
        source_location:
          "Controller:Synth/Program:PRG_A/Routine:Recipe_Calc/Rung[10]",
        routine: "Recipe_Calc",
        rung_number: 10,
        statement_index: null,
        block_name: null,
        language: "ladder",
        instruction_sequence: ["SIZE", "SUB"],
        readable_expression: "MaxRecipeNum = SIZE(Internal_Recipes[0]) - 1",
        input_signals: [
          {
            signal_id: "tag::Synth/PRG_A/Internal_Recipes[0]",
            signal_name: "Internal_Recipes[0]",
            instruction_type: "SIZE",
            relationship_id: "rel::read::size::source",
          },
        ],
        output_signals: [
          {
            signal_id: "tag::Synth/PRG_A/MaxRecipeNum",
            signal_name: "MaxRecipeNum",
            instruction_type: "SUB",
            relationship_id: "rel::write::sub::dest",
          },
        ],
        write_operations: [
          {
            signal_id: "tag::Synth/PRG_A/MaxRecipeNum",
            signal_name: "MaxRecipeNum",
            instruction_type: "SUB",
            write_behavior: "calculates",
            relationship_id: "rel::write::sub::dest",
          },
        ],
        confidence: 0.92,
        warnings: [],
        metadata: { path_kind: "calculation" },
      },
    ],
    data_source_reads: [
      {
        source_id: "rung::Synth/PRG_A/Recipe_Calc/10",
        source_name: "Rung 10",
        source_type: "rung",
        target_id: "tag::Synth/PRG_A/Internal_Recipes[0]",
        target_name: "Internal_Recipes[0]",
        relationship_type: "reads",
        source_location:
          "Controller:Synth/Program:PRG_A/Routine:Recipe_Calc/Rung[10]/Instruction:SIZE",
        instruction_type: "SIZE",
        write_behavior: null,
        condition_signal_ids: [],
        condition_signal_names: [],
        confidence: 0.92,
        metadata: {
          relationship_id: "rel::read::size::source",
          operand_semantic_role: "data_source_read",
        },
      },
    ],
    write_operations: [
      {
        source_id: "rung::Synth/PRG_A/Recipe_Calc/10",
        source_name: "Rung 10",
        source_type: "rung",
        target_id: "tag::Synth/PRG_A/MaxRecipeNum",
        target_name: "MaxRecipeNum",
        relationship_type: "writes",
        source_location:
          "Controller:Synth/Program:PRG_A/Routine:Recipe_Calc/Rung[10]/Instruction:SIZE",
        instruction_type: "SIZE",
        write_behavior: "moves_value",
        condition_signal_ids: [],
        condition_signal_names: [],
        confidence: 0.92,
        metadata: {
          relationship_id: "rel::write::size::dest",
          operand_semantic_role: "derived_calculation",
        },
      },
      {
        source_id: "rung::Synth/PRG_A/Recipe_Calc/10",
        source_name: "Rung 10",
        source_type: "rung",
        target_id: "tag::Synth/PRG_A/MaxRecipeNum",
        target_name: "MaxRecipeNum",
        relationship_type: "writes",
        source_location:
          "Controller:Synth/Program:PRG_A/Routine:Recipe_Calc/Rung[10]/Instruction:SUB",
        instruction_type: "SUB",
        write_behavior: "calculates",
        condition_signal_ids: [],
        condition_signal_names: [],
        confidence: 0.92,
        metadata: {
          relationship_id: "rel::write::sub::dest",
          operand_semantic_role: "derived_calculation",
        },
      },
    ],
    derived_calculations: [],
    derived_explanation:
      "MaxRecipeNum is calculated from Internal_Recipes[0]. SIZE gets the recipe array length, then SUB subtracts 1, making MaxRecipeNum the highest valid recipe index.",
    unknown_direction_references: [],
  },
  writer_rungs: [],
  upstream_required_conditions: [],
  downstream_readers: [],
  unknown_direction_blocks: [],
};

describe("SignalTroubleshootingWorkspaceView", () => {
  it("renders the five signal intelligence sections", () => {
    render(<SignalTroubleshootingWorkspaceView workspace={unifiedWorkspace} />);
    expect(screen.getByRole("heading", { name: "What controls this?" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "What does this control?" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Current state explanation" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Evidence sources" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Engineer/documentation knowledge" })).toBeInTheDocument();
    expect(screen.getAllByText("Output_B").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Permissive_A/).length).toBeGreaterThan(0);
  });

  it("renders ladder and FBD verification groups", () => {
    render(<SignalTroubleshootingWorkspaceView workspace={unifiedWorkspace} />);
    expect(screen.getByText(/Ladder evidence/)).toBeInTheDocument();
    expect(screen.getByText(/FBD evidence/)).toBeInTheDocument();
    expect(screen.getByText(/AOI evidence/)).toBeInTheDocument();
    expect(screen.getAllByText(/Rung 2/).length).toBeGreaterThan(0);
    expect(screen.getByText(/FBD output pin/)).toBeInTheDocument();
    expect(screen.getAllByText(/Pin Out/).length).toBeGreaterThan(0);
  });

  it("renders logic path cards instead of a flat boolean tag list", () => {
    render(<SignalTroubleshootingWorkspaceView workspace={unifiedWorkspace} />);
    expect(
      screen.getByRole("heading", { name: "What controls this?" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/IF Permissive_A THEN Output_B = TRUE/)).toBeInTheDocument();
    expect(screen.getByText(/Inputs: Permissive_A/)).toBeInTheDocument();
    expect(screen.getByText(/Writes: Output_B/)).toBeInTheDocument();
    const controlsSection = screen
      .getByRole("heading", { name: "What controls this?" })
      .closest("section");
    expect(controlsSection).not.toBeNull();
    expect(
      within(controlsSection as HTMLElement).queryByRole("heading", {
        name: "Permissive_A",
      }),
    ).not.toBeInTheDocument();
  });

  it("renders derived calculation logic paths with distinct styling", () => {
    render(<SignalTroubleshootingWorkspaceView workspace={calculationWorkspace} />);
    expect(
      screen.getByText("MaxRecipeNum = SIZE(Internal_Recipes[0]) - 1"),
    ).toBeInTheDocument();
    expect(screen.getByText("Calculation")).toBeInTheDocument();
    expect(screen.getByText(/Inputs: Internal_Recipes\[0\]/)).toBeInTheDocument();
  });

  it("shows unknown references separately from logic paths", () => {
    render(<SignalTroubleshootingWorkspaceView workspace={unifiedWorkspace} />);
    expect(screen.getByText(/Unknown-direction references/)).toBeInTheDocument();
    expect(screen.getAllByText("Generic_Block").length).toBeGreaterThan(0);
    expect(screen.queryByText(/IF.*Generic_Block/)).not.toBeInTheDocument();
  });

  it("keeps advanced relationship metadata collapsed by default", () => {
    render(<SignalTroubleshootingWorkspaceView workspace={unifiedWorkspace} />);
    expect(screen.getAllByText("Advanced details")).toHaveLength(1);
    expect(screen.queryByText("rel::write::ladder")).not.toBeInTheDocument();
  });

  it("renders calculation logic paths separately from boolean gating", () => {
    render(<SignalTroubleshootingWorkspaceView workspace={calculationWorkspace} />);
    expect(
      screen.getByText("MaxRecipeNum = SIZE(Internal_Recipes[0]) - 1"),
    ).toBeInTheDocument();
    expect(screen.getByText("Calculation")).toBeInTheDocument();
    expect(screen.getByText(/Inputs: Internal_Recipes\[0\]/)).toBeInTheDocument();
    expect(screen.getByText(/highest valid recipe index/i)).toBeInTheDocument();
    const controlsSection = screen
      .getByRole("heading", { name: "What controls this?" })
      .closest("section");
    expect(controlsSection).not.toBeNull();
    expect(
      within(controlsSection as HTMLElement).queryByText("Internal_Recipes[0]"),
    ).not.toBeInTheDocument();
  });

  it("shows live values on logic path input signals", () => {
    const workspace: SignalTroubleshootingWorkspace = {
      ...unifiedWorkspace,
      current_state_explanation: {
        status: "live_data_available",
        target_current_value: {
          signal_id: "tag::Synth/PRG_A/Output_B",
          signal_name: "Output_B",
          value: false,
          quality: "good",
          source: "OPC_UA",
        },
        upstream_condition_current_values: [
          {
            signal_id: "tag::Synth/PRG_A/Permissive_A",
            signal_name: "Permissive_A",
            value: false,
            quality: "good",
            source: "OPC_UA",
          },
        ],
        blocking_conditions: [],
        satisfied_conditions: [],
        stale_values: [],
        missing_values: [],
        confidence_contribution: 0.18,
      },
    };
    render(<SignalTroubleshootingWorkspaceView workspace={workspace} />);
    expect(screen.getByText(/Permissive_A FALSE/)).toBeInTheDocument();
    expect(screen.getByText(/Live FALSE/)).toBeInTheDocument();
  });
});
