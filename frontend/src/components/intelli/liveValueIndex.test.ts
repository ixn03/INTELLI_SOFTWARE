import type { SignalTroubleshootingWorkspace } from "@/types/reasoning";

import {
  buildLiveValueIndex,
  formatLiveValue,
  lookupLiveValue,
} from "./liveValueIndex";

const workspaceWithLive: SignalTroubleshootingWorkspace = {
  question: "Why is P101_RunCmd not on?",
  interpretation: {
    intent: "why_not_energized",
    target_signal_candidates: [],
    selected_target_signal: null,
    confidence: 0.8,
    metadata: {},
  },
  target_signal: {
    id: "tag::PLC/PRG/P101_RunCmd",
    name: "P101_RunCmd",
    object_type: "tag",
    source_location: null,
  },
  current_state_explanation: {
    status: "live_data_available",
    target_current_value: {
      signal_id: "tag::PLC/PRG/P101_RunCmd",
      signal_name: "P101_RunCmd",
      value: false,
      quality: "good",
      source: "OPC_UA",
    },
    upstream_condition_current_values: [
      {
        signal_id: "tag::PLC/PRG/Permissive_A",
        signal_name: "Permissive_A",
        value: false,
        quality: "good",
        source: "OPC_UA",
      },
    ],
    blocking_conditions: [
      {
        signal_id: "tag::PLC/PRG/Permissive_A",
        signal_name: "Permissive_A",
        value: false,
        quality: "good",
        source: "OPC_UA",
      },
    ],
    satisfied_conditions: [],
    stale_values: [],
    missing_values: [],
    confidence_contribution: 0.18,
  },
  writer_rungs: [],
  upstream_required_conditions: [],
  downstream_readers: [],
  unknown_direction_blocks: [],
  confidence_summary: {
    confidence: 0.8,
    evidence: [],
    missing_evidence: [],
    warnings: [],
  },
  deterministic_explanation: "Test",
  advanced_details: {},
};

describe("liveValueIndex", () => {
  it("indexes live values by signal name and id", () => {
    const index = buildLiveValueIndex(workspaceWithLive);
    expect(lookupLiveValue(index, ["P101_RunCmd"])?.value).toBe(false);
    expect(lookupLiveValue(index, ["tag::PLC/PRG/Permissive_A"])?.value).toBe(
      false,
    );
  });

  it("formats boolean live values for display", () => {
    expect(formatLiveValue(true)).toBe("TRUE");
    expect(formatLiveValue(false)).toBe("FALSE");
  });
});
