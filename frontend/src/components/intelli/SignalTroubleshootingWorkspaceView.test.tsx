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

describe("SignalTroubleshootingWorkspaceView", () => {
  it("renders answer-first troubleshooting sections", () => {
    render(<SignalTroubleshootingWorkspaceView workspace={unifiedWorkspace} />);
    expect(
      screen.getByRole("heading", { name: "What controls this signal?" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Written by" })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Where is it used?" }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("Output_B").length).toBeGreaterThan(0);
    expect(screen.getByText("Permissive_A")).toBeInTheDocument();
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

  it("shows unknown references separately from controls", () => {
    render(<SignalTroubleshootingWorkspaceView workspace={unifiedWorkspace} />);
    expect(screen.getAllByText(/unknown-direction block/i).length).toBeGreaterThan(
      0,
    );
    expect(screen.getAllByText("Generic_Block").length).toBeGreaterThan(0);
    const controlsColumn = screen
      .getByRole("heading", { name: "What controls this signal?" })
      .parentElement?.parentElement;
    expect(controlsColumn).not.toBeNull();
    expect(
      within(controlsColumn as HTMLElement).queryByText("Generic_Block"),
    ).not.toBeInTheDocument();
  });

  it("keeps advanced relationship metadata collapsed by default", () => {
    render(<SignalTroubleshootingWorkspaceView workspace={unifiedWorkspace} />);
    expect(screen.getAllByText("Advanced details")).toHaveLength(1);
    expect(screen.queryByText("rel::write::ladder")).not.toBeInTheDocument();
  });
});
