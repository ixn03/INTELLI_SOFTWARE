import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import axios from "axios";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ControlDocumentIntegrityWorkspace from "./ControlDocumentIntegrityWorkspace";

vi.mock("axios");

let mockSearchParams = new URLSearchParams();
const mockReplace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockReplace }),
  useSearchParams: () => mockSearchParams,
}));

vi.mock("@/context/IntelliProjectContext", () => ({
  useIntelliProject: () => ({ apiBase: "http://api.test" }),
  extractIntelliError: (_err: unknown, fallback: string) => fallback,
}));

const unit = {
  id: "unit-1",
  name: "Converting Area",
  area: "600B",
  description: null,
  created_at: "2026-06-25T00:00:00Z",
  updated_at: "2026-06-25T00:00:00Z",
};

const moduleRecord = {
  id: "module-1",
  process_unit_id: "unit-1",
  name: "Filtrate Separator",
  module_type: "DeltaV Equipment Module",
  description: null,
  aliases: [],
  metadata_json: {},
  created_at: "2026-06-25T00:00:00Z",
  updated_at: "2026-06-25T00:00:00Z",
};

const snapshot = {
  id: "snapshot-2",
  module_id: "module-1",
  source_type: "fhx",
  source_filename: "FILTRATE_SEPARATOR.fhx",
  file_hash: "hash-2",
  export_timestamp: null,
  parsed_extract: {},
  raw_project_id: "hash-2",
  notes: null,
  created_by: "engineer",
  created_at: "2026-06-25T00:00:00Z",
};

const engineeringRecord = {
  id: "record-1",
  process_unit_id: "unit-1",
  module_id: "module-1",
  record_type: "control_narrative",
  title: "Filtrate Separator control narrative",
  status: "needs_review",
  owner: "controls",
  description: null,
  metadata_json: {},
  created_at: "2026-06-25T00:00:00Z",
  updated_at: "2026-06-25T00:00:00Z",
};

const approvedRevision = {
  id: "revision-1",
  engineering_record_id: "record-1",
  source_logic_snapshot_id: "snapshot-1",
  template_id: null,
  revision: "1.0",
  status: "approved",
  body_markdown: "Approved mix sequence is 20 minutes.",
  structured_content: {},
  created_by: "engineer",
  approved_by: "lead",
  approved_at: "2026-06-25T00:00:00Z",
  created_at: "2026-06-25T00:00:00Z",
};

const reviewItem = {
  id: "review-1",
  process_unit_id: "unit-1",
  module_id: "module-1",
  engineering_record_id: "record-1",
  logic_snapshot_id: "snapshot-2",
  logic_diff_id: "diff-1",
  proposed_document_update_id: "proposal-1",
  title: "Review Filtrate Separator control narrative",
  reason: "tags +2 -0; routines +1 -0",
  status: "open",
  assigned_to: null,
  created_at: "2026-06-25T00:00:00Z",
  resolved_at: null,
};

const logicDiff = {
  id: "diff-1",
  module_id: "module-1",
  previous_snapshot_id: "snapshot-1",
  current_snapshot_id: "snapshot-2",
  changed: true,
  changed_routines: [{ change: "routine_added", name: "MIX" }],
  changed_tags: [{ change: "tag_added", name: "MIX_TIMER" }],
  changed_reads_writes: [],
  summary_payload: { summary: "tags +2 -0; routines +1 -0" },
  impacted_record_ids: ["record-1"],
  created_at: "2026-06-25T00:00:00Z",
};

const proposedUpdate = {
  id: "proposal-1",
  engineering_record_id: "record-1",
  source_logic_snapshot_id: "snapshot-2",
  source_logic_diff_id: "diff-1",
  base_document_revision_id: "revision-1",
  proposed_body: null,
  proposed_structured_changes: {
    diff_summary: "tags +2 -0; routines +1 -0",
    instruction: "Engineer review required.",
  },
  confidence: "deterministic",
  status: "proposed",
  created_at: "2026-06-25T00:00:00Z",
};

const importSource = {
  id: "source-1",
  process_unit_id: "unit-1",
  module_id: "module-1",
  name: "Manual XML export",
  source_system: "xml_export",
  acquisition_mode: "manual_upload",
  connector_hint: null,
  endpoint_url: null,
  export_path: null,
  schedule: null,
  is_enabled: true,
  config_json: {},
  last_run_at: null,
  created_at: "2026-06-25T00:00:00Z",
  updated_at: "2026-06-25T00:00:00Z",
};

const importResult = {
  run: {
    id: "run-1",
    source_id: "source-1",
    module_id: "module-1",
    status: "completed",
    acquired_filename: "FILTRATE_SEPARATOR.L5X",
    project_id: "hash-2",
    previous_project_id: "hash-1",
    snapshot_id: "snapshot-2",
    diff_summary: "tags +2 -0; routines +1 -0",
    impacted_records: [],
    error_message: null,
    started_at: "2026-06-25T00:00:00Z",
    completed_at: "2026-06-25T00:00:00Z",
  },
  snapshot,
  diff: { summary: "tags +2 -0; routines +1 -0" },
  logic_diff: logicDiff,
  impacted_records: [],
  process_unit_id: "unit-1",
  module_id: "module-1",
  import_source_id: "source-1",
  snapshot_id: "snapshot-2",
  diff_id: "diff-1",
  changed: true,
  review_item_ids: ["review-1"],
  affected_record_ids: ["record-1"],
};

function mockGet(url: string) {
  if (url.endsWith("/api/process-units")) {
    return Promise.resolve({ data: { items: [unit], total: 1 } });
  }
  if (url.endsWith("/api/process-units/unit-1/modules")) {
    return Promise.resolve({ data: [moduleRecord] });
  }
  if (url.endsWith("/api/process-units/unit-1/review-items")) {
    return Promise.resolve({ data: [reviewItem] });
  }
  if (url.endsWith("/api/modules/module-1/record")) {
    return Promise.resolve({
      data: {
        module: moduleRecord,
        snapshots: [snapshot],
        narratives: [],
        governance_artifacts: [],
        issues: [],
      },
    });
  }
  if (url.endsWith("/api/engineering-records")) {
    return Promise.resolve({ data: [engineeringRecord] });
  }
  if (url.endsWith("/api/engineering-records/record-1/revisions")) {
    return Promise.resolve({ data: [approvedRevision] });
  }
  if (url.endsWith("/api/snapshots/snapshot-2/diff")) {
    return Promise.resolve({ data: logicDiff });
  }
  if (url.endsWith("/api/proposed-document-updates/proposal-1")) {
    return Promise.resolve({ data: proposedUpdate });
  }
  return Promise.reject(new Error(`Unexpected GET ${url}`));
}

describe("ControlDocumentIntegrityWorkspace", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    mockSearchParams = new URLSearchParams();
    vi.mocked(axios.get).mockImplementation((url) => mockGet(String(url)));
    vi.mocked(axios.post).mockResolvedValue({
      data: {
        id: "approval-1",
        review_item_id: "review-1",
        engineering_record_id: "record-1",
        document_revision_id: null,
        proposed_document_update_id: "proposal-1",
        decision: "approved",
        actor: "controls.engineer",
        comments: "ok",
        created_at: "2026-06-25T00:00:00Z",
      },
    });
  });

  it("renders the process unit list", async () => {
    render(<ControlDocumentIntegrityWorkspace />);

    expect((await screen.findAllByText("Converting Area")).length).toBeGreaterThan(0);
    expect(screen.getByText("600B")).toBeInTheDocument();
    expect(screen.getAllByText("Filtrate Separator").length).toBeGreaterThan(0);
  });

  it("renders the import upload form", async () => {
    render(<ControlDocumentIntegrityWorkspace />);

    expect(await screen.findByText("Import latest control export")).toBeInTheDocument();
    expect(screen.getByLabelText("Export file")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Import and create review" })).toBeInTheDocument();
  });

  it("renders the module record page with engineering records", async () => {
    render(<ControlDocumentIntegrityWorkspace />);

    expect(await screen.findByText("Current logic snapshot")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getAllByText(/FILTRATE_SEPARATOR/).length).toBeGreaterThan(0);
    });
    expect(screen.getByText("Filtrate Separator control narrative")).toBeInTheDocument();
    expect(screen.getByText(/Latest approved revision: 1.0/)).toBeInTheDocument();
  });

  it("renders review items and proposed update details", async () => {
    render(<ControlDocumentIntegrityWorkspace />);

    expect(
      await screen.findByText("Review Filtrate Separator control narrative"),
    ).toBeInTheDocument();
    expect(screen.getByText("Approved mix sequence is 20 minutes.")).toBeInTheDocument();
    expect(screen.getAllByText(/tags \+2 -0; routines \+1 -0/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/deterministic/).length).toBeGreaterThan(0);
  });

  it("approve action calls the API", async () => {
    render(<ControlDocumentIntegrityWorkspace />);

    const approve = await screen.findByRole("button", { name: "Approve" });
    fireEvent.click(approve);

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        "http://api.test/api/review-items/review-1/approve",
        expect.objectContaining({ actor: "controls.engineer" }),
      );
    });
  });

  it("reject action calls the API", async () => {
    render(<ControlDocumentIntegrityWorkspace />);

    const reject = await screen.findByRole("button", { name: "Reject" });
    fireEvent.click(reject);

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        "http://api.test/api/review-items/review-1/reject",
        expect.objectContaining({ actor: "controls.engineer" }),
      );
    });
  });

  it("renders successful import summary and review workspace link", async () => {
    vi.mocked(axios.post).mockImplementation((url) => {
      if (String(url).endsWith("/api/import-sources")) {
        return Promise.resolve({ data: importSource });
      }
      if (String(url).endsWith("/api/import-sources/source-1/sync-upload")) {
        return Promise.resolve({ data: importResult });
      }
      return Promise.reject(new Error(`Unexpected POST ${String(url)}`));
    });
    render(<ControlDocumentIntegrityWorkspace />);

    const input = await screen.findByLabelText("Export file");
    fireEvent.change(input, {
      target: { files: [new File(["<xml />"], "FILTRATE_SEPARATOR.L5X")] },
    });
    fireEvent.click(screen.getByRole("button", { name: "Import and create review" }));

    expect(await screen.findByText("Import complete")).toBeInTheDocument();
    expect(screen.getByText("Changes found")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Review Workspace" })).toHaveAttribute(
      "href",
      "/workspace/integrity?unitId=unit-1&moduleId=module-1",
    );
  });

  it("selects a module from URL query params", async () => {
    mockSearchParams = new URLSearchParams("unitId=unit-1&moduleId=module-1");
    render(<ControlDocumentIntegrityWorkspace />);

    await screen.findByText("Current logic snapshot");
    expect(axios.get).toHaveBeenCalledWith("http://api.test/api/modules/module-1/record");
  });
});
