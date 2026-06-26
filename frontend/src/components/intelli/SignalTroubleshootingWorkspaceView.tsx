import type { ReactNode } from "react";

import type {
  FBDEvidenceGroup,
  LadderEvidenceGroup,
  LogicPath,
  SignalTroubleshootingWorkspace,
  UnifiedSignalEvidence,
} from "@/types/reasoning";

import {
  buildLogicPathsFromEvidence,
  formatLogicPathExpression,
  formatLogicPathSource,
  formatSignalList,
  isCalculationPath,
} from "./logicPaths";
import {
  buildLiveValueIndex,
  formatLiveValue,
  liveValueBadgeTone,
  lookupLiveValue,
  type LiveValueIndex,
  type SignalLiveValue,
} from "./liveValueIndex";
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
  const liveIndex = buildLiveValueIndex(workspace);
  const targetName =
    unified?.target_signal_name ??
    workspace.target_signal?.name ??
    workspace.interpretation.selected_target_signal?.name ??
    workspace.target_signal?.id ??
    "Unresolved signal";

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <SignalHeader
        targetName={targetName}
        workspace={workspace}
        unified={unified}
        liveIndex={liveIndex}
      />
      <WhatControlsSection workspace={workspace} unified={unified} liveIndex={liveIndex} />
      <WhatControlsSectionLegacyBuckets workspace={workspace} />
      <WhatThisControlsSection workspace={workspace} unified={unified} />
      <CurrentStateSection workspace={workspace} />
      <EvidenceSourcesSection workspace={workspace} unified={unified} />
      <KnowledgeSection workspace={workspace} />

      <details className="rounded-lg border border-zinc-800/80 bg-zinc-950/55">
        <summary className="cursor-pointer px-5 py-3 text-sm font-medium text-zinc-200">
          Advanced details
        </summary>
        <LevelThreeAdvanced workspace={workspace} unified={unified} />
      </details>
    </div>
  );
}

function SignalHeader({
  targetName,
  workspace,
  unified,
  liveIndex,
}: {
  targetName: string;
  workspace: SignalTroubleshootingWorkspace;
  unified: UnifiedSignalEvidence | null;
  liveIndex: LiveValueIndex;
}) {
  const confidence =
    unified?.confidence_summary.confidence ??
    workspace.confidence_summary.confidence;
  const scope = workspace.resolved_scope;
  const targetLive = lookupLiveValue(liveIndex, [
    workspace.target_signal?.id,
    workspace.target_signal?.name,
    targetName,
  ]);

  return (
    <section className="rounded-lg border border-zinc-800/80 bg-zinc-950/55">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-zinc-800/70 px-5 py-4">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
            Signal intelligence
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-semibold text-white">{targetName}</h2>
            {targetLive ? (
              <LiveValueBadge live={targetLive} label="Live" />
            ) : workspace.current_state_explanation?.status === "live_data_missing" ? (
              <Badge tone="warning">Live missing</Badge>
            ) : null}
          </div>
          <p className="mt-2 text-sm leading-6 text-zinc-300">
            {unified?.summary.answer ?? workspace.deterministic_explanation}
          </p>
        </div>
        <ConfidencePill value={confidence} />
      </div>
      {scope ? (
        <div className="flex flex-wrap gap-2 px-5 py-3">
          <Badge tone="outline">Controller: {scope.controller ?? "unknown"}</Badge>
          <Badge tone="outline">Program: {scope.program ?? "unknown"}</Badge>
          <Badge tone={scope.duplicate_name_status === "duplicates_found" ? "warning" : "neutral"}>
            {scope.duplicate_name_status.replaceAll("_", " ")}
          </Badge>
        </div>
      ) : null}
    </section>
  );
}

function WhatControlsSection({
  workspace,
  unified,
  liveIndex,
}: {
  workspace: SignalTroubleshootingWorkspace;
  unified: UnifiedSignalEvidence | null;
  liveIndex: LiveValueIndex;
}) {
  const controls =
    workspace.what_controls_this_signal?.upstream_required_conditions ??
    workspace.upstream_required_conditions;
  const hasSemanticControlBuckets = workspace.what_controls_this_signal != null;
  const unifiedControls = hasSemanticControlBuckets
    ? []
    : (unified?.what_controls_this_signal ?? []);
  const controlCount = controls.length || unifiedControls.length;
  const writeOperations =
    workspace.what_controls_this_signal?.write_operations ?? workspace.writer_rungs;
  const logicPaths: LogicPath[] =
    workspace.what_controls_this_signal?.logic_paths?.length
      ? workspace.what_controls_this_signal.logic_paths
      : buildLogicPathsFromEvidence(controls, writeOperations);
  const unknowns =
    workspace.what_controls_this_signal?.unknown_direction_references ??
    workspace.unknown_direction_blocks;
  const derivedExplanation =
    workspace.what_controls_this_signal?.derived_explanation ?? null;
  const writers = workspace.who_writes_this_signal ?? {
    ladder: workspace.writer_rungs,
    fbd: [],
    aoi: [],
    structured_text: [],
    sfc: [],
    unknown: [],
  };

  return (
    <section className="rounded-lg border border-zinc-800/80 bg-zinc-950/55">
      <SectionTitle
        title="What controls this?"
        detail="Each logic path is one rung, ST statement, or block that writes this signal. Inputs and write behavior are reconstructed from the normalized program model."
      />
      <div className="space-y-4 p-5">
        {derivedExplanation ? (
          <p className="rounded-lg border border-sky-900/40 bg-sky-950/20 px-4 py-3 text-sm leading-6 text-sky-100">
            {derivedExplanation}
          </p>
        ) : null}
        {logicPaths.length ? (
          <div className="grid gap-4 lg:grid-cols-2">
            {logicPaths.map((path) => (
              <LogicPathCard key={path.id} path={path} liveIndex={liveIndex} />
            ))}
          </div>
        ) : unifiedControls.length ? (
          <div className="grid gap-4 lg:grid-cols-2">
            {unifiedControls.map((item, idx) => (
              <MiniCard
                key={`unified-control-${idx}`}
                title={item.signal_name ?? "Unknown signal"}
                subtitle={formatProvenance(item.source_provenance)}
                confidence={item.confidence}
                extra={
                  item.signal_name ? (
                    <ConditionLiveValues
                      names={[item.signal_name]}
                      ids={[item.signal_id]}
                      liveIndex={liveIndex}
                    />
                  ) : null
                }
              />
            ))}
          </div>
        ) : (
          <EmptyState title="No deterministic logic paths were found for this signal." />
        )}
        {unknowns.length ? (
          <details className="rounded-lg border border-amber-900/30 bg-amber-950/10">
            <summary className="cursor-pointer px-4 py-3 text-sm font-medium text-amber-100">
              Unknown-direction references ({unknowns.length})
            </summary>
            <div className="grid gap-3 p-4 pt-0">
              {unknowns.map((item) => (
                <EvidenceMini key={evidenceKey(item)} item={item} warning />
              ))}
            </div>
          </details>
        ) : null}
      </div>
      <div className="border-t border-zinc-800/70 px-5 py-3">
        <EvidenceSourceChips counts={unified?.evidence_sources ?? fallbackEvidenceCounts(writers)} />
      </div>
    </section>
  );
}

function WhatControlsSectionLegacyBuckets({
  workspace,
}: {
  workspace: SignalTroubleshootingWorkspace;
}) {
  const dataSources = workspace.what_controls_this_signal?.data_source_reads ?? [];
  const writeOperations =
    workspace.what_controls_this_signal?.write_operations ?? workspace.writer_rungs;
  const derivedCalculations =
    workspace.what_controls_this_signal?.derived_calculations ?? [];
  const dependencies =
    workspace.what_controls_this_signal?.upstream_dependencies ?? [];
  if (
    !dataSources.length &&
    !writeOperations.length &&
    !derivedCalculations.length &&
    !dependencies.length
  ) {
    return null;
  }
  return (
    <details className="rounded-lg border border-zinc-800/80 bg-zinc-950/55">
      <summary className="cursor-pointer px-5 py-3 text-sm font-medium text-zinc-300">
        Legacy evidence buckets (advanced)
      </summary>
      <div className="grid gap-4 p-5 lg:grid-cols-2">
        {dependencies.length ? (
          <AnswerColumn title="Upstream dependencies" empty="" count={dependencies.length}>
            {dependencies.map((item) => <EvidenceMini key={evidenceKey(item)} item={item} />)}
          </AnswerColumn>
        ) : null}
        {dataSources.length ? (
          <AnswerColumn title="Data sources" empty="" count={dataSources.length}>
            {dataSources.map((item) => (
              <EvidenceMini key={evidenceKey(item)} item={item} />
            ))}
          </AnswerColumn>
        ) : null}
        {writeOperations.length ? (
          <AnswerColumn title="Write operations" empty="" count={writeOperations.length}>
            {writeOperations.map((item) => (
              <EvidenceMini key={`write-${evidenceKey(item)}`} item={item} />
            ))}
          </AnswerColumn>
        ) : null}
        {derivedCalculations.length ? (
          <AnswerColumn title="Derived calculations" empty="" count={derivedCalculations.length}>
            {derivedCalculations.map((item) => (
              <EvidenceMini key={`derived-${evidenceKey(item)}`} item={item} />
            ))}
          </AnswerColumn>
        ) : null}
      </div>
    </details>
  );
}

function WhatThisControlsSection({
  workspace,
}: {
  workspace: SignalTroubleshootingWorkspace;
  unified: UnifiedSignalEvidence | null;
}) {
  const impact = workspace.what_this_signal_controls;
  const readers = impact?.downstream_readers ?? workspace.downstream_readers;
  const influenced = impact?.downstream_writes_influenced ?? [];
  const fbd = impact?.downstream_fbd_blocks ?? [];
  const st = impact?.downstream_st_statements ?? [];

  return (
    <section className="rounded-lg border border-zinc-800/80 bg-zinc-950/55">
      <SectionTitle title="What does this control?" detail="Downstream readers and writes influenced by this signal through shared routines or blocks." />
      <div className="grid gap-4 p-5 lg:grid-cols-2 xl:grid-cols-4">
        <AnswerColumn title="Downstream usage" empty="No downstream usage was found." count={readers.length}>
          {readers.map((item) => <EvidenceMini key={evidenceKey(item)} item={item} />)}
        </AnswerColumn>
        <AnswerColumn title="Writes influenced" empty="No downstream writes were influenced by this signal in the normalized graph." count={influenced.length}>
          {influenced.map((item) => <EvidenceMini key={evidenceKey(item)} item={item} />)}
        </AnswerColumn>
        <AnswerColumn title="FBD/AOI blocks" empty="No downstream FBD or AOI blocks were found." count={fbd.length + (impact?.downstream_aoi_blocks.length ?? 0)}>
          {[...fbd, ...(impact?.downstream_aoi_blocks ?? [])].map((item) => <EvidenceMini key={evidenceKey(item)} item={item} />)}
        </AnswerColumn>
        <AnswerColumn title="ST statements" empty="No downstream ST statements were found." count={st.length}>
          {st.map((item) => <EvidenceMini key={evidenceKey(item)} item={item} />)}
        </AnswerColumn>
      </div>
    </section>
  );
}

function CurrentStateSection({ workspace }: { workspace: SignalTroubleshootingWorkspace }) {
  const state = workspace.current_state_explanation;
  const liveMeta = workspace.advanced_details?.live_data as
    | {
        enabled?: boolean;
        resolved?: number;
        missing_registry?: number;
        missing_influx?: number;
        source?: string;
      }
    | undefined;
  const target = state?.target_current_value as
    | {
        signal_name?: string | null;
        value?: unknown;
        timestamp?: string | null;
        quality?: string | null;
        source?: string | null;
      }
    | null
    | undefined;
  return (
    <section className="rounded-lg border border-zinc-800/80 bg-zinc-950/55">
      <SectionTitle
        title="Current state explanation"
        detail={
          liveMeta?.enabled
            ? `Live values from tag registry + InfluxDB (${liveMeta.resolved ?? 0} resolved).`
            : "Live values are used when supplied or when the tag registry has matching samples."
        }
      />
      <div className="grid gap-4 p-5 lg:grid-cols-4">
        <StatusCard
          title="Status"
          value={state?.status?.replaceAll("_", " ") ?? "live data missing"}
        />
        <div className="rounded-lg border border-zinc-800/80 bg-zinc-900/35 p-4">
          <h3 className="text-sm font-semibold text-zinc-100">Target live value</h3>
          {target ? (
            <p className="mt-2 text-sm text-zinc-300">
              {target.signal_name ?? "Target"}: <span className="font-mono">{String(target.value)}</span>
              {target.quality ? ` · ${target.quality}` : ""}
              {target.source ? ` · ${target.source}` : ""}
            </p>
          ) : (
            <p className="mt-2 text-sm text-zinc-500">No live value for target signal.</p>
          )}
        </div>
        <AnswerColumn title="Blocking conditions" empty="No blocking live conditions are known." count={state?.blocking_conditions.length ?? 0} warning>
          {(state?.blocking_conditions ?? []).map((item, idx) => <LiveValueMini key={`blocking-${idx}`} item={item} warning />)}
        </AnswerColumn>
        <AnswerColumn title="Satisfied conditions" empty="No satisfied live conditions are known." count={state?.satisfied_conditions.length ?? 0}>
          {(state?.satisfied_conditions ?? []).map((item, idx) => <LiveValueMini key={`satisfied-${idx}`} item={item} />)}
        </AnswerColumn>
      </div>
      {(state?.missing_values?.length ?? 0) > 0 ? (
        <div className="border-t border-zinc-800/80 px-5 py-3 text-xs text-zinc-500">
          Missing live values:{" "}
          {state?.missing_values.map((item) => item.name ?? item.id).join(", ")}
        </div>
      ) : null}
    </section>
  );
}

function EvidenceSourcesSection({
  workspace,
  unified,
}: {
  workspace: SignalTroubleshootingWorkspace;
  unified: UnifiedSignalEvidence | null;
}) {
  const provenance = workspace.where_evidence_comes_from ?? [];
  return (
    <section className="rounded-lg border border-zinc-800/80 bg-zinc-950/55">
      <SectionTitle title="Evidence sources" detail="Ladder, FBD, AOI, ST, SFC, and unknown provenance for every relationship INTELLI used." />
      <div className="space-y-3 p-5">
        {unified ? <LevelTwoVerification unified={unified} /> : null}
        <AnswerColumn title="Relationship provenance" empty="No relationship provenance is available." count={provenance.length}>
          {provenance.map((item) => (
            <MiniCard
              key={item.relationship_id}
              title={`${item.language.toUpperCase()} ${item.relationship_type}`}
              subtitle={[item.routine, item.rung != null ? `Rung ${item.rung}` : null, item.block, item.pin ? `Pin ${item.pin}` : null, item.statement ? `Statement ${item.statement}` : null].filter(Boolean).join(" · ") || item.source_location}
              confidence={item.confidence}
              badges={[item.deterministic ? "deterministic" : "direction unknown"]}
              warning={!item.deterministic}
            />
          ))}
        </AnswerColumn>
      </div>
    </section>
  );
}

function KnowledgeSection({ workspace }: { workspace: SignalTroubleshootingWorkspace }) {
  const context = workspace.knowledge_context;
  return (
    <section className="rounded-lg border border-zinc-800/80 bg-zinc-950/55">
      <SectionTitle title="Engineer/documentation knowledge" detail="Engineer notes and documentation facts supplement deterministic logic; they do not override parsed evidence." />
      <div className="grid gap-4 p-5 lg:grid-cols-3">
        <KnowledgeBucket title="Engineer notes" items={context?.engineer_notes ?? []} />
        <KnowledgeBucket title="Control narrative facts" items={context?.control_narrative_facts ?? []} />
        <KnowledgeBucket title="Documentation facts" items={context?.documentation_facts ?? []} />
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

function LogicPathCard({
  path,
  liveIndex,
}: {
  path: LogicPath;
  liveIndex: LiveValueIndex;
}) {
  const calculation = isCalculationPath(path);
  const writes = path.write_operations.length
    ? formatSignalList(
        path.write_operations.map((write) => ({
          signal_id: write.signal_id,
          signal_name: write.signal_name,
          instruction_type: write.instruction_type,
          relationship_id: write.relationship_id,
        })),
      )
    : formatSignalList(path.output_signals);

  return (
    <div
      className={`rounded-lg border p-3 ${
        calculation
          ? "border-sky-800/70 bg-sky-950/20"
          : "border-zinc-800/80 bg-zinc-900/45"
      }`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={calculation ? "info" : "neutral"}>
          {formatLogicPathSource(path)}
        </Badge>
        {calculation ? <Badge tone="outline">Calculation</Badge> : null}
        <Badge tone="neutral">{Math.round(path.confidence * 100)}%</Badge>
      </div>
      <p className="mt-2 font-mono text-sm leading-6 text-zinc-100">
        {formatLogicPathExpression(path)}
      </p>
      {path.input_signals.length ? (
        <p className="mt-2 text-xs text-zinc-400">
          Inputs: {formatSignalList(path.input_signals)}
        </p>
      ) : null}
      {writes !== "—" ? (
        <p className="mt-1 text-xs text-zinc-400">Writes: {writes}</p>
      ) : null}
      {path.warnings.length ? (
        <div className="mt-2 space-y-1">
          {path.warnings.map((warning) => (
            <p key={warning} className="text-xs text-amber-200/85">
              {warning}
            </p>
          ))}
        </div>
      ) : null}
      {path.input_signals.length ? (
        <ConditionLiveValues
          names={path.input_signals.map(
            (signal) => signal.signal_name ?? signal.signal_id,
          )}
          ids={path.input_signals.map((signal) => signal.signal_id)}
          liveIndex={liveIndex}
        />
      ) : null}
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

function SectionTitle({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="border-b border-zinc-800/70 px-5 py-4">
      <h3 className="text-base font-semibold text-white">{title}</h3>
      <p className="mt-1 text-sm leading-6 text-zinc-400">{detail}</p>
    </div>
  );
}

function EvidenceMini({
  item,
  warning = false,
  liveIndex,
  showConditionLiveValues = false,
}: {
  item: SignalTroubleshootingWorkspace["writer_rungs"][number];
  warning?: boolean;
  liveIndex?: LiveValueIndex;
  showConditionLiveValues?: boolean;
}) {
  const targetLive =
    liveIndex &&
    lookupLiveValue(liveIndex, [item.target_id, item.target_name]);
  return (
    <MiniCard
      title={item.target_name ?? item.source_name ?? "Unknown signal"}
      subtitle={
        formatWriterSubtitle({
          source_provenance: {
            routine: item.source_name,
            instruction_type: item.instruction_type,
            source_location: item.source_location,
          },
          condition_signal_names: item.condition_signal_names,
        }) || formatEvidenceItem(item)
      }
      confidence={item.confidence}
      badges={[item.relationship_type, item.instruction_type, item.write_behavior]}
      warning={warning}
      extra={
        <>
          {targetLive ? <LiveValueBadge live={targetLive} /> : null}
          {showConditionLiveValues && liveIndex ? (
            <ConditionLiveValues
              names={item.condition_signal_names}
              ids={item.condition_signal_ids}
              liveIndex={liveIndex}
            />
          ) : null}
        </>
      }
    />
  );
}

function ConditionLiveValues({
  names,
  ids,
  liveIndex,
}: {
  names: string[];
  ids?: string[];
  liveIndex: LiveValueIndex;
}) {
  if (!names.length) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-2">
      {names.map((name, idx) => {
        const live = lookupLiveValue(liveIndex, [name, ids?.[idx]]);
        if (!live) {
          return (
            <Badge key={`${name}-missing`} tone="neutral">
              {name}: no live
            </Badge>
          );
        }
        return <LiveValueBadge key={`${name}-live`} live={live} label={name} />;
      })}
    </div>
  );
}

function LiveValueBadge({
  live,
  label,
}: {
  live: SignalLiveValue;
  label?: string;
}) {
  const tone = liveValueBadgeTone(live);
  const prefix = label ? `${label} ` : "";
  return (
    <Badge tone={tone}>
      {prefix}
      {formatLiveValue(live.value)}
      {live.source ? ` · ${live.source}` : ""}
    </Badge>
  );
}

function LiveValueMini({ item, warning = false }: { item: unknown; warning?: boolean }) {
  const value = item as {
    signal_name?: string | null;
    signal_id?: string;
    value?: unknown;
    timestamp?: string | null;
    quality?: string | null;
    source?: string | null;
    canonical_name?: string | null;
  };
  const label = value.canonical_name ?? value.signal_name ?? value.signal_id ?? "Unknown signal";
  const extras = [value.quality, value.source].filter(Boolean).join(" · ");
  return (
    <MiniCard
      title={label}
      subtitle={`Value: ${String(value.value)}${value.timestamp ? ` · ${value.timestamp}` : ""}${extras ? ` · ${extras}` : ""}`}
      warning={warning}
    />
  );
}

function StatusCard({ title, value }: { title: string; value: string }) {
  return (
    <div className="rounded-lg border border-zinc-800/80 bg-zinc-900/35 p-4">
      <h3 className="text-sm font-semibold text-zinc-100">{title}</h3>
      <p className="mt-2 text-sm text-zinc-300">{value}</p>
    </div>
  );
}

function KnowledgeBucket({ title, items }: { title: string; items: unknown[] }) {
  return (
    <AnswerColumn title={title} empty="No linked knowledge facts were found." count={items.length}>
      {items.map((item, idx) => {
        const fact = item as { statement?: string; knowledge_type?: string; source?: string };
        return (
          <MiniCard
            key={`${title}-${idx}`}
            title={fact.knowledge_type?.replaceAll("_", " ") ?? "Knowledge fact"}
            subtitle={fact.statement ?? fact.source ?? "Linked knowledge item"}
          />
        );
      })}
    </AnswerColumn>
  );
}

function fallbackEvidenceCounts(
  writers: NonNullable<SignalTroubleshootingWorkspace["who_writes_this_signal"]>,
): UnifiedSignalEvidence["evidence_sources"] {
  return {
    ladder: writers.ladder.length,
    fbd: writers.fbd.length,
    structured_text: writers.structured_text.length,
    sfc: writers.sfc.length,
    aoi: writers.aoi.length,
    unknown: writers.unknown.length,
  };
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
  extra,
}: {
  title: string;
  subtitle?: string | null;
  confidence?: number;
  badges?: (string | null | undefined)[];
  warning?: boolean;
  extra?: ReactNode;
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
      {extra}
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

function formatEvidenceItem(
  item: SignalTroubleshootingWorkspace["writer_rungs"][number],
): string {
  const parts = [
    item.source_name,
    item.source_location,
    item.instruction_type,
  ].filter(Boolean);
  return parts.join(" · ");
}

function evidenceKey(
  item: SignalTroubleshootingWorkspace["writer_rungs"][number],
): string {
  const relId = item.metadata.relationship_id;
  return typeof relId === "string"
    ? relId
    : `${item.source_id}-${item.target_id}-${item.relationship_type}`;
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
