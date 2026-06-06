import type { ReactNode } from "react";

import type {
  FBDEvidenceGroup,
  LadderEvidenceGroup,
  SignalTroubleshootingWorkspace,
  UnifiedSignalEvidence,
} from "@/types/reasoning";

import { Badge, Code, EmptyState } from "./ui";

export function SignalTroubleshootingWorkspaceView({
  workspace,
}: {
  workspace: SignalTroubleshootingWorkspace | null;
}) {
  if (!workspace) {
    return (
      <div className="grid h-full min-h-[28rem] place-items-center rounded-lg border border-dashed border-zinc-800 bg-zinc-950/35 p-8 text-center">
        <div className="max-w-md">
          <p className="text-lg font-semibold text-zinc-100">
            Ask a controls question to troubleshoot a signal.
          </p>
          <p className="mt-2 text-sm leading-6 text-zinc-500">
            INTELLI resolves the target signal, shows what controls it, who
            writes it, where it is used, and groups evidence by ladder, FBD,
            AOI, and ST for engineer verification.
          </p>
        </div>
      </div>
    );
  }

  const unified = workspace.unified_evidence ?? null;
  const targetName =
    unified?.target_signal_name ??
    workspace.target_signal?.name ??
    workspace.interpretation.selected_target_signal?.name ??
    workspace.target_signal?.id ??
    "Unresolved signal";

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <LevelOneAnswer
        targetName={targetName}
        workspace={workspace}
        unified={unified}
      />

      {unified ? <LevelTwoVerification unified={unified} /> : null}

      <details className="rounded-lg border border-zinc-800/80 bg-zinc-950/55">
        <summary className="cursor-pointer px-5 py-3 text-sm font-medium text-zinc-200">
          Advanced details
        </summary>
        <LevelThreeAdvanced workspace={workspace} unified={unified} />
      </details>
    </div>
  );
}

function LevelOneAnswer({
  targetName,
  workspace,
  unified,
}: {
  targetName: string;
  workspace: SignalTroubleshootingWorkspace;
  unified: UnifiedSignalEvidence | null;
}) {
  const confidence =
    unified?.confidence_summary.confidence ??
    workspace.confidence_summary.confidence;
  const answer =
    unified?.summary.answer ?? workspace.deterministic_explanation;

  const controls =
    unified?.what_controls_this_signal ??
    workspace.upstream_required_conditions.map((item) => ({
      signal_name: item.target_name,
      source_provenance: {
        originating_language: "ladder" as const,
        routine: null,
        rung_number: null,
        instruction_type: item.instruction_type,
        source_location: item.source_location,
      },
      confidence: item.confidence,
    }));

  const writers =
    unified?.who_writes_this_signal ??
    workspace.writer_rungs.map((item) => ({
      writer_type: "ladder_rung",
      signal_name: item.target_name,
      source_provenance: {
        originating_language: "ladder" as const,
        routine: item.source_name,
        rung_number: null,
        instruction_type: item.instruction_type,
        source_location: item.source_location,
      },
      condition_signal_names: item.condition_signal_names,
      confidence: item.confidence,
      write_behavior: item.write_behavior,
    }));

  const readers =
    unified?.where_is_it_used ??
    workspace.downstream_readers.map((item) => ({
      signal_name: item.target_name,
      source_provenance: {
        originating_language: "ladder" as const,
        routine: item.source_name,
        source_location: item.source_location,
        instruction_type: item.instruction_type,
      },
      confidence: item.confidence,
    }));

  const unknowns =
    unified?.unknowns ??
    workspace.unknown_direction_blocks.map((item) => ({
      message:
        "Referenced by unknown-direction block. INTELLI preserved the relationship but did not infer causality.",
      reference_source_name: item.source_name,
      source_provenance: { source_location: item.source_location },
      confidence: item.confidence,
    }));

  return (
    <section className="rounded-lg border border-zinc-800/80 bg-zinc-950/55">
      <div className="border-b border-zinc-800/70 px-5 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
              Signal troubleshooting
            </p>
            <h2 className="mt-1 text-lg font-semibold text-white">
              {targetName}
            </h2>
          </div>
          <ConfidencePill value={confidence} />
        </div>
        <p className="mt-3 text-sm leading-6 text-zinc-300">{answer}</p>
      </div>

      <div className="grid gap-4 p-5 lg:grid-cols-2 xl:grid-cols-4">
        <AnswerColumn
          title="What controls this signal?"
          empty="No upstream required conditions were found."
          count={controls.length}
        >
          {controls.map((item, idx) => (
            <MiniCard
              key={`control-${idx}`}
              title={item.signal_name ?? "Unknown signal"}
              subtitle={formatProvenance(item.source_provenance)}
              confidence={item.confidence}
            />
          ))}
        </AnswerColumn>

        <AnswerColumn
          title="Written by"
          empty="No deterministic writer was found."
          count={writers.length}
        >
          {writers.map((item, idx) => (
            <MiniCard
              key={`writer-${idx}`}
              title={item.signal_name ?? targetName}
              subtitle={formatWriterSubtitle(item)}
              confidence={item.confidence}
              badges={[
                item.write_behavior,
                item.source_provenance?.instruction_type,
              ].filter(Boolean)}
            />
          ))}
        </AnswerColumn>

        <AnswerColumn
          title="Where is it used?"
          empty="No downstream readers were found."
          count={readers.length}
        >
          {readers.map((item, idx) => (
            <MiniCard
              key={`reader-${idx}`}
              title={item.signal_name ?? targetName}
              subtitle={formatProvenance(item.source_provenance)}
              confidence={item.confidence}
            />
          ))}
        </AnswerColumn>

        <AnswerColumn
          title="Unknowns"
          empty="No direction-unknown references were found."
          count={unknowns.length}
          warning
        >
          {unknowns.map((item, idx) => (
            <MiniCard
              key={`unknown-${idx}`}
              title={item.reference_source_name ?? "Unknown reference"}
              subtitle={item.message}
              confidence={item.confidence}
              warning
            />
          ))}
        </AnswerColumn>
      </div>

      {unified ? (
        <div className="border-t border-zinc-800/70 px-5 py-3">
          <EvidenceSourceChips counts={unified.evidence_sources} />
        </div>
      ) : null}

      <div className="border-t border-zinc-800/70 px-5 py-4">
        <MissingEvidencePanel
          missing={
            unified?.confidence_summary.missing_evidence ??
            workspace.confidence_summary.missing_evidence
          }
          warnings={
            unified?.confidence_summary.warnings ??
            workspace.confidence_summary.warnings
          }
        />
      </div>
    </section>
  );
}

function LevelTwoVerification({
  unified,
}: {
  unified: UnifiedSignalEvidence;
}) {
  const { verification } = unified;

  return (
    <section className="rounded-lg border border-zinc-800/80 bg-zinc-950/55">
      <div className="border-b border-zinc-800/70 px-5 py-4">
        <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
          Engineer verification
        </p>
        <p className="mt-1 text-sm text-zinc-400">
          Evidence grouped by source language. Expand each section to verify
          ladder rungs, FBD blocks/pins/wires, AOI parameters, or ST
          statements.
        </p>
      </div>

      <div className="space-y-3 p-5">
        <VerificationSection title="Ladder evidence" count={verification.ladder.length}>
          {verification.ladder.map((item, idx) => (
            <LadderEvidenceCard key={`ladder-${idx}`} item={item} />
          ))}
        </VerificationSection>

        <VerificationSection title="FBD evidence" count={verification.fbd.length}>
          {verification.fbd.map((item, idx) => (
            <FBDEvidenceCard key={`fbd-${idx}`} item={item} />
          ))}
        </VerificationSection>

        <VerificationSection title="AOI evidence" count={verification.aoi.length}>
          {verification.aoi.map((item, idx) => (
            <div
              key={`aoi-${idx}`}
              className="rounded-lg border border-zinc-800/80 bg-zinc-900/45 p-3 text-sm text-zinc-200"
            >
              <p>
                AOI parameter{" "}
                <span className="font-medium text-white">
                  {item.parameter_name ?? "unknown"}
                </span>{" "}
                on {item.aoi_instance ?? "instance"}
              </p>
              {item.signal_name ? (
                <p className="mt-1 text-xs text-zinc-400">
                  Signal: {item.signal_name}
                </p>
              ) : null}
              {item.source_location ? (
                <p className="mt-2 font-mono text-[11px] text-zinc-500">
                  {item.source_location}
                </p>
              ) : null}
            </div>
          ))}
        </VerificationSection>

        <VerificationSection
          title="SFC evidence"
          count={verification.sfc?.length ?? 0}
        >
          {(verification.sfc ?? []).map((item, idx) => (
            <div
              key={`sfc-${idx}`}
              className="rounded-lg border border-zinc-800/80 bg-zinc-900/45 p-3 text-sm text-zinc-200"
            >
              <p>
                {item.role} in {item.routine ?? "routine"}
                {item.step_name ? ` · Step ${item.step_name}` : ""}
              </p>
              {item.structural_only ? (
                <p className="mt-1 text-xs text-amber-200/80">
                  This relationship is structural only.
                </p>
              ) : null}
            </div>
          ))}
        </VerificationSection>

        <VerificationSection
          title="ST evidence"
          count={verification.structured_text.length}
        >
          {verification.structured_text.map((item, idx) => (
            <div
              key={`st-${idx}`}
              className="rounded-lg border border-zinc-800/80 bg-zinc-900/45 p-3 text-sm text-zinc-200"
            >
              <p>
                {item.role === "assignment" ? "Assignment" : "Read"} in{" "}
                {item.routine ?? "routine"}
                {item.statement_index != null
                  ? ` statement ${item.statement_index}`
                  : ""}
              </p>
              {item.signal_name ? (
                <p className="mt-1 text-xs text-zinc-400">
                  Signal: {item.signal_name}
                </p>
              ) : null}
            </div>
          ))}
        </VerificationSection>

        <VerificationSection
          title="Unknown references"
          count={verification.unknown.length}
          warning
        >
          {verification.unknown.map((item, idx) => (
            <MiniCard
              key={`unk-${idx}`}
              title={item.reference_source ?? "Unknown block"}
              subtitle={item.message}
              confidence={item.confidence}
              warning
            />
          ))}
        </VerificationSection>
      </div>
    </section>
  );
}

function LevelThreeAdvanced({
  workspace,
  unified,
}: {
  workspace: SignalTroubleshootingWorkspace;
  unified: UnifiedSignalEvidence | null;
}) {
  return (
    <div className="grid gap-4 border-t border-zinc-800/70 p-5 lg:grid-cols-2">
      <div>
        <p className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-zinc-500">
          Confidence scoring
        </p>
        <Code>
          {JSON.stringify(
            unified?.confidence_summary ?? workspace.confidence_summary,
            null,
            2,
          )}
        </Code>
      </div>
      <div>
        <p className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-zinc-500">
          Relationship IDs and source object IDs
        </p>
        <Code>
          {JSON.stringify(
            unified?.advanced_details ?? workspace.advanced_details,
            null,
            2,
          )}
        </Code>
      </div>
    </div>
  );
}

function LadderEvidenceCard({ item }: { item: LadderEvidenceGroup }) {
  const roleLabel =
    item.role === "writer"
      ? "Written by"
      : item.role === "condition"
        ? "Required condition"
        : "Read by";
  return (
    <div className="rounded-lg border border-zinc-800/80 bg-zinc-900/45 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="neutral">{roleLabel}</Badge>
        {item.instruction_type ? (
          <Badge tone="outline">{item.instruction_type}</Badge>
        ) : null}
      </div>
      <p className="mt-2 text-sm text-zinc-200">
        {item.routine ?? "Routine"}
        {item.rung_number != null ? ` · Rung ${item.rung_number}` : ""}
      </p>
      {item.condition_signal_names.length ? (
        <p className="mt-1 text-xs text-zinc-400">
          Rung condition: {item.condition_signal_names.join(", ")}
        </p>
      ) : null}
      {item.source_location ? (
        <p className="mt-2 font-mono text-[11px] text-zinc-500">
          {item.source_location}
        </p>
      ) : null}
    </div>
  );
}

function FBDEvidenceCard({ item }: { item: FBDEvidenceGroup }) {
  const roleLabel =
    item.role === "output"
      ? "FBD output pin"
      : item.role === "input"
        ? "FBD input pin"
        : item.role === "wire"
          ? "FBD wire connection"
          : "FBD reference";
  return (
    <div className="rounded-lg border border-zinc-800/80 bg-zinc-900/45 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="info">{roleLabel}</Badge>
        {item.structural_only ? (
          <Badge tone="warning">Structural only</Badge>
        ) : null}
      </div>
      <p className="mt-2 text-sm text-zinc-200">
        {item.block_name ?? "Block"}
        {item.pin_name ? ` · Pin ${item.pin_name}` : ""}
      </p>
      {item.connected_signal_name ? (
        <p className="mt-1 text-xs text-zinc-400">
          Connected signal: {item.connected_signal_name}
        </p>
      ) : null}
      {item.source_location ? (
        <p className="mt-2 font-mono text-[11px] text-zinc-500">
          {item.source_location}
        </p>
      ) : null}
    </div>
  );
}

function AnswerColumn({
  title,
  empty,
  count,
  warning = false,
  children,
}: {
  title: string;
  empty: string;
  count: number;
  warning?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="min-w-0 rounded-lg border border-zinc-800/80 bg-zinc-900/35 p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-zinc-100">{title}</h3>
        <Badge tone={warning ? "warning" : "outline"}>{count}</Badge>
      </div>
      {count === 0 ? <EmptyState title={empty} /> : <div className="space-y-2">{children}</div>}
    </div>
  );
}

function VerificationSection({
  title,
  count,
  warning = false,
  children,
}: {
  title: string;
  count: number;
  warning?: boolean;
  children: ReactNode;
}) {
  if (count === 0) {
    return null;
  }
  return (
    <details className="rounded-lg border border-zinc-800/80 bg-zinc-900/35">
      <summary className="cursor-pointer px-4 py-3 text-sm font-semibold text-zinc-100">
        <span className="inline-flex items-center gap-2">
          {title}
          <Badge tone={warning ? "warning" : "outline"}>{count}</Badge>
        </span>
      </summary>
      <div className="space-y-2 border-t border-zinc-800/70 p-4">{children}</div>
    </details>
  );
}

function MiniCard({
  title,
  subtitle,
  confidence,
  badges = [],
  warning = false,
}: {
  title: string;
  subtitle?: string | null;
  confidence?: number;
  badges?: (string | null | undefined)[];
  warning?: boolean;
}) {
  return (
    <div
      className={`rounded-lg border p-3 ${
        warning
          ? "border-amber-800/70 bg-amber-950/20"
          : "border-zinc-800/80 bg-zinc-900/45"
      }`}
    >
      <div className="flex flex-wrap items-center gap-2">
        {badges.map((badge) =>
          badge ? (
            <Badge key={badge} tone="outline">
              {badge}
            </Badge>
          ) : null,
        )}
        {confidence != null ? (
          <Badge tone="neutral">{Math.round(confidence * 100)}%</Badge>
        ) : null}
      </div>
      <p className="mt-2 text-sm font-medium text-white">{title}</p>
      {subtitle ? (
        <p className="mt-1 text-xs leading-5 text-zinc-400">{subtitle}</p>
      ) : null}
    </div>
  );
}

function EvidenceSourceChips({
  counts,
}: {
  counts: UnifiedSignalEvidence["evidence_sources"];
}) {
  const entries = (
    [
      ["Ladder", counts.ladder],
      ["FBD", counts.fbd],
      ["ST", counts.structured_text],
      ["SFC", counts.sfc],
      ["AOI", counts.aoi],
      ["Unknown", counts.unknown],
    ] as [string, number][]
  ).filter(([, n]) => n > 0);

  if (!entries.length) return null;

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-xs text-zinc-500">Evidence sources</span>
      {entries.map(([label, count]) => (
        <Badge key={label} tone="outline">
          {label}: {count}
        </Badge>
      ))}
    </div>
  );
}

function MissingEvidencePanel({
  missing,
  warnings,
}: {
  missing: string[];
  warnings: string[];
}) {
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <div>
        <h3 className="mb-2 text-sm font-semibold text-zinc-100">
          Missing evidence
        </h3>
        <ul className="space-y-1">
          {missing.map((item) => (
            <li
              key={item}
              className="rounded-lg border border-zinc-800 bg-zinc-900/45 px-3 py-2 text-xs text-zinc-400"
            >
              {item.replaceAll("_", " ")}
            </li>
          ))}
        </ul>
      </div>
      {warnings.length ? (
        <div>
          <h3 className="mb-2 text-sm font-semibold text-amber-100">
            Warnings
          </h3>
          <ul className="space-y-1">
            {warnings.map((item) => (
              <li
                key={item}
                className="rounded-lg border border-amber-800/70 bg-amber-950/20 px-3 py-2 text-xs text-amber-100"
              >
                {item}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function formatProvenance(
  prov:
    | {
        routine?: string | null;
        rung_number?: number | null;
        instruction_type?: string | null;
        source_location?: string | null;
        block_name?: string | null;
        pin_name?: string | null;
      }
    | undefined,
): string {
  if (!prov) return "";
  const parts: string[] = [];
  if (prov.routine) parts.push(prov.routine);
  if (prov.rung_number != null) parts.push(`Rung ${prov.rung_number}`);
  if (prov.block_name) parts.push(prov.block_name);
  if (prov.pin_name) parts.push(`Pin ${prov.pin_name}`);
  if (prov.instruction_type) parts.push(prov.instruction_type);
  if (parts.length) return parts.join(" · ");
  return prov.source_location ?? "";
}

function formatWriterSubtitle(item: {
  source_provenance?: {
    routine?: string | null;
    rung_number?: number | null;
    instruction_type?: string | null;
    block_name?: string | null;
    pin_name?: string | null;
    source_location?: string | null;
  };
  condition_signal_names?: string[];
}): string {
  const base = formatProvenance(item.source_provenance);
  if (item.condition_signal_names?.length) {
    return `${base} · Required: ${item.condition_signal_names.join(", ")}`;
  }
  return base;
}

function ConfidencePill({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const tone = value >= 0.8 ? "success" : value >= 0.55 ? "warning" : "danger";
  return (
    <Badge tone={tone} uppercase>
      {pct}% confidence
    </Badge>
  );
}
