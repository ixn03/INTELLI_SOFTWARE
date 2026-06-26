import type {
  LogicLineGroup,
  SignalEvidenceItem,
} from "@/types/reasoning";

function writerId(item: SignalEvidenceItem): string | null {
  const id = item.metadata?.writer_relationship_id;
  return typeof id === "string" ? id : null;
}

function relationshipId(item: SignalEvidenceItem): string | null {
  const id = item.metadata?.relationship_id;
  return typeof id === "string" ? id : null;
}

function requiredValue(instructionType: string | null): boolean | null {
  const upper = (instructionType ?? "").toUpperCase();
  if (upper === "XIC") return true;
  if (upper === "XIO") return false;
  return null;
}

function languageForItem(item: SignalEvidenceItem): LogicLineGroup["language"] {
  const sourceType = (item.source_type ?? "").toLowerCase();
  const location = (item.source_location ?? "").toLowerCase();
  const instruction = (item.instruction_type ?? "").toLowerCase();
  if (sourceType === "rung" || location.includes("rung[")) return "ladder";
  if (sourceType.includes("structured") || location.includes("statement")) {
    return "structured_text";
  }
  if (sourceType.includes("sfc") || location.includes("/sfc")) return "sfc";
  if (sourceType.includes("aoi") || location.includes("aoi")) return "aoi";
  if (
    sourceType.includes("function_block") ||
    sourceType.includes("fbd") ||
    location.includes("block:")
  ) {
    return "fbd";
  }
  return "unknown";
}

function extractRoutine(sourceLocation: string | null): string | null {
  if (!sourceLocation) return null;
  const match = sourceLocation.match(/Routine:([^/]+)/);
  return match?.[1] ?? null;
}

function extractRung(sourceLocation: string | null): number | null {
  if (!sourceLocation) return null;
  const match = sourceLocation.match(/Rung\[(\d+)\]/);
  return match ? Number(match[1]) : null;
}

function extractStatementIndex(sourceLocation: string | null): number | null {
  if (!sourceLocation) return null;
  const match = sourceLocation.match(/Statement[:[](\d+)/);
  return match ? Number(match[1]) : null;
}

function synthesizeLogicLine(
  conditions: LogicLineGroup["conditions"],
  writer: SignalEvidenceItem | undefined,
): string {
  const parts = conditions.map((condition) => {
    const name = condition.signal_name ?? condition.signal_id;
    const itype = (condition.instruction_type ?? "XIC").toUpperCase();
    if (itype === "XIO" || condition.required_value === false) {
      return `NOT ${name}`;
    }
    return `${itype}(${name})`;
  });
  const gate = parts.length ? parts.join(" AND ") : "TRUE";
  if (writer) {
    const output = writer.instruction_type ?? "OUT";
    const target = writer.target_name ?? writer.target_id;
    return `${gate} → ${output}(${target})`;
  }
  return gate;
}

/** Client-side fallback when API has not yet populated logic_line_groups. */
export function buildLogicLineGroupsFromEvidence(
  conditions: SignalEvidenceItem[],
  writers: SignalEvidenceItem[],
): LogicLineGroup[] {
  const writerByRelId = new Map(
    writers
      .map((writer) => [relationshipId(writer), writer] as const)
      .filter((entry): entry is [string, SignalEvidenceItem] => Boolean(entry[0])),
  );

  const grouped = new Map<string, SignalEvidenceItem[]>();
  for (const item of conditions) {
    const key = writerId(item) ?? item.source_id;
    const bucket = grouped.get(key) ?? [];
    bucket.push(item);
    grouped.set(key, bucket);
  }

  const groups: LogicLineGroup[] = [];
  for (const writer of writers) {
    const relId = relationshipId(writer);
    const key = relId ?? writer.source_id;
    const conditionItems = grouped.get(key) ?? [];
    const lineConditions = conditionItems.map((item) => ({
      signal_id: item.target_id,
      signal_name: item.target_name,
      instruction_type: item.instruction_type,
      required_value: requiredValue(item.instruction_type),
      relationship_id: relationshipId(item),
    }));
    groups.push({
      writer_relationship_id: relId ?? writer.source_id,
      source_id: writer.source_id,
      source_name: writer.source_name,
      source_type: writer.source_type,
      language: languageForItem(writer),
      source_location: writer.source_location,
      routine: extractRoutine(writer.source_location),
      rung_number: extractRung(writer.source_location),
      statement_index: extractStatementIndex(writer.source_location),
      instruction_type: writer.instruction_type,
      write_behavior: writer.write_behavior,
      logic_text: synthesizeLogicLine(lineConditions, writer),
      output_summary: writer.instruction_type
        ? `${writer.instruction_type} writes ${writer.target_name ?? writer.target_id}`
        : null,
      conditions: lineConditions,
      confidence: Math.min(writer.confidence, ...conditionItems.map((i) => i.confidence)),
    });
  }

  if (groups.length) return groups;

  for (const [sourceId, conditionItems] of grouped) {
    const lineConditions = conditionItems.map((item) => ({
      signal_id: item.target_id,
      signal_name: item.target_name,
      instruction_type: item.instruction_type,
      required_value: requiredValue(item.instruction_type),
      relationship_id: relationshipId(item),
    }));
    const sample = conditionItems[0];
    groups.push({
      writer_relationship_id: writerId(sample) ?? sourceId,
      source_id: sourceId,
      source_name: sample.source_name,
      source_type: sample.source_type,
      language: languageForItem(sample),
      source_location: sample.source_location,
      routine: extractRoutine(sample.source_location),
      rung_number: extractRung(sample.source_location),
      statement_index: extractStatementIndex(sample.source_location),
      instruction_type: sample.instruction_type,
      write_behavior: null,
      logic_text: synthesizeLogicLine(lineConditions, undefined),
      output_summary: null,
      conditions: lineConditions,
      confidence: Math.min(...conditionItems.map((i) => i.confidence)),
    });
  }

  return groups;
}

export function formatLogicLineLocation(group: LogicLineGroup): string {
  const parts: string[] = [];
  if (group.routine) parts.push(group.routine);
  if (group.rung_number != null) parts.push(`Rung ${group.rung_number}`);
  if (group.statement_index != null) parts.push(`Statement ${group.statement_index}`);
  if (group.source_name && !parts.length) parts.push(group.source_name);
  return parts.join(" · ") || group.source_location || "Unknown location";
}

export function languageLabel(language: LogicLineGroup["language"]): string {
  switch (language) {
    case "ladder":
      return "Ladder";
    case "structured_text":
      return "Structured Text";
    case "fbd":
      return "FBD";
    case "aoi":
      return "AOI";
    case "sfc":
      return "SFC";
    default:
      return "Logic";
  }
}
