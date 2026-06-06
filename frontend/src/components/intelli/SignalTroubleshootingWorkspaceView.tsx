import type {
  SignalEvidenceItem,
  SignalTroubleshootingWorkspace,
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
            Ask a controls question to build the signal workspace.
          </p>
          <p className="mt-2 text-sm leading-6 text-zinc-500">
            INTELLI will resolve the target signal, find writer rungs, show
            upstream conditions, and list downstream readers with deterministic
            evidence.
          </p>
        </div>
      </div>
    );
  }

  const targetName =
    workspace.target_signal?.name ??
    workspace.interpretation.selected_target_signal?.name ??
    workspace.target_signal?.id ??
    "Unresolved signal";

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <div className="grid min-h-0 flex-1 gap-4 xl:grid-cols-[minmax(0,1.35fr)_minmax(22rem,0.75fr)]">
        <section className="min-h-0 rounded-lg border border-zinc-800/80 bg-zinc-950/55">
          <div className="border-b border-zinc-800/70 px-5 py-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
                  Signal relationship map
                </p>
                <h2 className="mt-1 text-lg font-semibold text-white">
                  {targetName}
                </h2>
              </div>
              <ConfidencePill value={workspace.confidence_summary.confidence} />
            </div>
          </div>

          <div className="grid gap-4 p-5 lg:grid-cols-[minmax(0,1fr)_minmax(13rem,0.8fr)_minmax(0,1fr)]">
            <RelationshipColumn
              title="What must be true?"
              empty="No upstream conditions were found for the writer rungs."
              items={workspace.upstream_required_conditions}
              itemPrefix="Blocks or permits"
            />

            <div className="flex min-h-48 flex-col items-center justify-center rounded-lg border border-cyan-400/30 bg-cyan-400/[0.06] p-4 text-center">
              <Badge tone="info" uppercase>
                Target signal
              </Badge>
              <p className="mt-3 max-w-full break-words text-xl font-semibold text-cyan-50">
                {targetName}
              </p>
              {workspace.target_signal?.source_location ? (
                <p className="mt-3 max-w-full break-words font-mono text-[11px] leading-5 text-cyan-100/70">
                  {workspace.target_signal.source_location}
                </p>
              ) : null}
            </div>

            <RelationshipColumn
              title="Where is it used?"
              empty="No downstream readers were found."
              items={workspace.downstream_readers}
              itemPrefix="This tag is used downstream here"
            />
          </div>
        </section>

        <EvidencePanel workspace={workspace} />
      </div>

      <details className="rounded-lg border border-zinc-800/80 bg-zinc-950/55">
        <summary className="cursor-pointer px-5 py-3 text-sm font-medium text-zinc-200">
          Advanced details
        </summary>
        <div className="grid gap-4 border-t border-zinc-800/70 p-5 lg:grid-cols-2">
          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-zinc-500">
              Confidence scoring
            </p>
            <Code>
              {JSON.stringify(workspace.confidence_summary, null, 2)}
            </Code>
          </div>
          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-zinc-500">
              Raw relationship IDs and parser metadata
            </p>
            <Code>{JSON.stringify(workspace.advanced_details, null, 2)}</Code>
          </div>
        </div>
      </details>
    </div>
  );
}

function RelationshipColumn({
  title,
  empty,
  items,
  itemPrefix,
}: {
  title: string;
  empty: string;
  items: SignalEvidenceItem[];
  itemPrefix: string;
}) {
  return (
    <div className="min-w-0 rounded-lg border border-zinc-800/80 bg-zinc-900/35 p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-zinc-100">{title}</h3>
        <Badge tone="outline">{items.length}</Badge>
      </div>
      {items.length === 0 ? (
        <EmptyState title={empty} />
      ) : (
        <div className="space-y-2">
          {items.map((item) => (
            <EvidenceMiniCard
              key={`${item.metadata.relationship_id ?? item.source_id}-${item.target_id}`}
              item={item}
              prefix={itemPrefix}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function EvidencePanel({
  workspace,
}: {
  workspace: SignalTroubleshootingWorkspace;
}) {
  return (
    <aside className="min-h-0 rounded-lg border border-zinc-800/80 bg-zinc-950/55">
      <div className="border-b border-zinc-800/70 px-5 py-4">
        <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
          Evidence
        </p>
        <p className="mt-1 text-sm leading-6 text-zinc-300">
          {workspace.deterministic_explanation}
        </p>
      </div>
      <div className="max-h-[calc(100vh-17rem)] space-y-4 overflow-auto p-5">
        <EvidenceSection
          title="Written by"
          empty="No deterministic writer was found."
          items={workspace.writer_rungs}
          prefix="This tag is written here"
        />
        <EvidenceSection
          title="Read by"
          empty="No downstream reads were found."
          items={workspace.downstream_readers}
          prefix="This tag is read here"
        />
        <EvidenceSection
          title="Unknowns"
          empty="No direction-unknown block references were found."
          items={workspace.unknown_direction_blocks}
          prefix="Direction is unknown here"
          warning
        />
        <ListPanel
          title="Missing evidence"
          items={workspace.confidence_summary.missing_evidence}
          empty="No missing evidence was reported."
        />
        {workspace.confidence_summary.warnings.length ? (
          <ListPanel
            title="Warnings"
            items={workspace.confidence_summary.warnings}
            empty=""
            warning
          />
        ) : null}
      </div>
    </aside>
  );
}

function EvidenceSection({
  title,
  empty,
  items,
  prefix,
  warning = false,
}: {
  title: string;
  empty: string;
  items: SignalEvidenceItem[];
  prefix: string;
  warning?: boolean;
}) {
  return (
    <section>
      <div className="mb-2 flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-zinc-100">{title}</h3>
        <Badge tone={warning ? "warning" : "outline"}>{items.length}</Badge>
      </div>
      {items.length === 0 ? (
        <EmptyState title={empty} />
      ) : (
        <div className="space-y-2">
          {items.map((item) => (
            <EvidenceMiniCard
              key={`${title}-${item.metadata.relationship_id ?? item.source_id}-${item.target_id}`}
              item={item}
              prefix={prefix}
              warning={warning}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function EvidenceMiniCard({
  item,
  prefix,
  warning = false,
}: {
  item: SignalEvidenceItem;
  prefix: string;
  warning?: boolean;
}) {
  const label = item.target_name ?? item.source_name ?? item.target_id;
  const source = item.source_name ?? item.source_id;
  return (
    <div
      className={`rounded-lg border p-3 ${
        warning
          ? "border-amber-800/70 bg-amber-950/20"
          : "border-zinc-800/80 bg-zinc-900/45"
      }`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={warning ? "warning" : "neutral"}>
          {item.relationship_type}
        </Badge>
        {item.instruction_type ? (
          <Badge tone="outline">{item.instruction_type}</Badge>
        ) : null}
        {item.write_behavior ? (
          <Badge tone="info">{item.write_behavior}</Badge>
        ) : null}
      </div>
      <p className="mt-2 text-sm leading-6 text-zinc-200">
        {prefix}: <span className="font-medium text-white">{label}</span>
      </p>
      <p className="mt-1 break-words text-xs text-zinc-500">{source}</p>
      {item.condition_signal_names.length ? (
        <p className="mt-2 text-xs leading-5 text-zinc-400">
          This rung energizes it when:{" "}
          <span className="text-zinc-200">
            {item.condition_signal_names.join(", ")}
          </span>
        </p>
      ) : null}
      {item.source_location ? (
        <p className="mt-2 break-words font-mono text-[11px] leading-5 text-zinc-500">
          {item.source_location}
        </p>
      ) : null}
    </div>
  );
}

function ListPanel({
  title,
  items,
  empty,
  warning = false,
}: {
  title: string;
  items: string[];
  empty: string;
  warning?: boolean;
}) {
  return (
    <section>
      <h3 className="mb-2 text-sm font-semibold text-zinc-100">{title}</h3>
      {items.length === 0 ? (
        <EmptyState title={empty} />
      ) : (
        <ul className="space-y-1">
          {items.map((item) => (
            <li
              key={item}
              className={`rounded-lg border px-3 py-2 text-xs ${
                warning
                  ? "border-amber-800/70 bg-amber-950/20 text-amber-100"
                  : "border-zinc-800 bg-zinc-900/45 text-zinc-300"
              }`}
            >
              {item.replaceAll("_", " ")}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
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
