import type {
  LogicLineGroup,
  LogicPath,
  LogicPathSignalRef,
  SignalEvidenceItem,
} from "@/types/reasoning";

import {
  buildLogicLineGroupsFromEvidence,
  formatLogicLineLocation,
  languageLabel,
} from "./logicLineGroups";

function signalRefFromCondition(
  condition: LogicLineGroup["conditions"][number],
): LogicPathSignalRef {
  return {
    signal_id: condition.signal_id,
    signal_name: condition.signal_name,
    instruction_type: condition.instruction_type,
    relationship_id: condition.relationship_id,
  };
}

/** Convert legacy logic_line_groups into LogicPath objects for display. */
export function logicPathsFromLineGroups(groups: LogicLineGroup[]): LogicPath[] {
  return groups.map((group) => ({
    id: group.writer_relationship_id,
    source_location: group.source_location,
    routine: group.routine,
    rung_number: group.rung_number,
    statement_index: group.statement_index,
    block_name: group.source_name,
    language: group.language,
    instruction_sequence: group.instruction_type ? [group.instruction_type] : [],
    readable_expression:
      group.logic_text ??
      (group.output_summary
        ? `${group.logic_text ?? ""} ${group.output_summary}`.trim()
        : group.output_summary),
    input_signals: group.conditions.map(signalRefFromCondition),
    output_signals: group.output_summary
      ? [
          {
            signal_id: group.writer_relationship_id,
            signal_name: group.output_summary.replace(/^.*writes\s+/i, ""),
            instruction_type: group.instruction_type,
            relationship_id: group.writer_relationship_id,
          },
        ]
      : [],
    write_operations: group.instruction_type
      ? [
          {
            signal_id: group.writer_relationship_id,
            signal_name: group.output_summary?.replace(/^.*writes\s+/i, "") ?? null,
            instruction_type: group.instruction_type,
            write_behavior: group.write_behavior,
            relationship_id: group.writer_relationship_id,
          },
        ]
      : [],
    confidence: group.confidence,
    warnings: [],
    metadata: {
      path_kind: group.conditions.length ? "boolean_control" : "direct_write",
      legacy_logic_line_group: true,
    },
  }));
}

/** Client-side fallback when API has not yet populated logic_paths. */
export function buildLogicPathsFromEvidence(
  conditions: SignalEvidenceItem[],
  writers: SignalEvidenceItem[],
): LogicPath[] {
  return logicPathsFromLineGroups(
    buildLogicLineGroupsFromEvidence(conditions, writers),
  );
}

export function formatLogicPathSource(path: LogicPath): string {
  const parts: string[] = [];
  if (path.rung_number != null) parts.push(`Rung[${path.rung_number}]`);
  if (path.statement_index != null) parts.push(`Statement[${path.statement_index}]`);
  if (path.block_name) parts.push(path.block_name);
  if (!parts.length) {
    return formatLogicLineLocation({
      writer_relationship_id: path.id,
      source_id: path.id,
      source_name: path.block_name,
      source_type: null,
      language: path.language,
      source_location: path.source_location,
      routine: path.routine,
      rung_number: path.rung_number,
      statement_index: path.statement_index,
      instruction_type: path.instruction_sequence[0] ?? null,
      write_behavior: null,
      logic_text: path.readable_expression,
      output_summary: null,
      conditions: [],
      confidence: path.confidence,
    });
  }
  return `${parts.join(" · ")} · ${languageLabel(path.language)}`;
}

export function isCalculationPath(path: LogicPath): boolean {
  return path.metadata?.path_kind === "calculation";
}

export function formatLogicPathExpression(path: LogicPath): string {
  if (path.readable_expression) return path.readable_expression;
  const inputs = path.input_signals
    .map((signal) => signal.signal_name ?? signal.signal_id)
    .join(", ");
  const writes = path.write_operations
    .map((write) => write.signal_name ?? write.signal_id)
    .join(", ");
  if (inputs && writes) return `Inputs: ${inputs} → Writes: ${writes}`;
  return writes || inputs || "Logic path";
}

export function formatSignalList(signals: LogicPathSignalRef[]): string {
  if (!signals.length) return "—";
  return signals
    .map((signal) => signal.signal_name ?? signal.signal_id)
    .join(", ");
}
