import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";

import ControlDocumentIntegrityWorkspace from "./ControlDocumentIntegrityWorkspace";
import { resetNavigationMocks } from "@/test/mocks/next-navigation";

const mockAxios = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  delete: vi.fn(),
  isAxiosError: () => false,
}));

vi.mock("axios", () => ({
  default: mockAxios,
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

const ioRecord = {
  id: "record-2",
  process_unit_id: "unit-1",
  module_id: "module-1",
  record_type: "io_list",
  title: "Filtrate Separator IO List",
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

const draftRevision = {
  id: "revision-2",
  engineering_record_id: "record-2",
  source_logic_snapshot_id: "snapshot-2",
  template_id: null,
  revision: "0.1",
  status: "draft",
  body_markdown: "## Tag\n\n- MIX_TIMER",
  structured_content: {
    generation: "deterministic_template",
    sections: [{ title: "Tag", facts: ["MIX_TIMER"], needs_engineer_input: false }],
  },
  created_by: "controls.engineer",
  approved_by: null,
  approved_at: null,
  created_at: "2026-06-25T00:00:00Z",
};

const aiDraftResponse = {
  revision: draftRevision,
  generation_mode: "llm_assisted",
  provider_name: "fake",
  confidence: "medium",
  source_snapshot_id: "snapshot-2",
  template_id: null,
  facts_used: [
    {
      key: "tags",
      label: "Tags",
      values: ["MIX_TIMER"],
      source_field: "LogicSnapshot.parsed_extract.tags",
      source_kind: "parsed_extract",
      source_snapshot_id: "snapshot-2",
      present: true,
    },
  ],
  missing_facts: [
    {
      key: "setpoints",
      label: "Setpoints",
      values: [],
      source_field: "LogicSnapshot.parsed_extract.setpoints",
      source_kind: "parsed_extract",
      source_snapshot_id: "snapshot-2",
      present: false,
    },
  ],
  assumptions: [],
  warnings: ["llm_assist_disabled"],
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
  if (url.endsWith("/api/import-sources")) {
    return Promise.resolve({ data: [importSource] });
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
    mockAxios.get.mockReset();
    mockAxios.post.mockReset();
    mockAxios.delete.mockReset();
    resetNavigationMocks();
    mockAxios.get.mockImplementation((url) => mockGet(String(url)));
    mockAxios.post.mockResolvedValue({
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

  it("renders the simplified guided page and workflow tabs", async () => {
    render(<ControlDocumentIntegrityWorkspace />);

    expect(screen.getByRole("heading", { name: "Control Integrity" })).toBeInTheDocument();
    expect(
      screen.getByText("Keep logic, narratives, IO lists, C&E, alarms, and MOC records synchronized."),
    ).toBeInTheDocument();
    expect(screen.getByText("Latest import")).toBeInTheDocument();
    expect(screen.getByText("Documents needing review")).toBeInTheDocument();
    expect(screen.getByText("Missing documents")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Plant Explorer/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Home/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Imports/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Review Queue/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Documents/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create new area/process unit" })).toBeInTheDocument();
    expect(await screen.findByText("Workspace status")).toBeInTheDocument();
  });

  it("renders the import upload form", async () => {
    render(<ControlDocumentIntegrityWorkspace />);

    fireEvent.click(screen.getByRole("button", { name: /Imports/ }));
    expect(await screen.findByText("Step 1 - Import Control Project")).toBeInTheDocument();
    expect(screen.getByText("Step 2 - Automatic Discovery")).toBeInTheDocument();
    expect(screen.getByLabelText("Export file")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Import Control Project" })).toBeInTheDocument();
  });

  it("creates a new area/process unit from the guided setup form", async () => {
    const createdUnit = { ...unit, id: "unit-2", name: "New Area", area: "700A" };
    mockAxios.post.mockImplementation((url, body) => {
      if (String(url).endsWith("/api/process-units")) {
        expect(body).toEqual({ name: "New Area", area: "700A", description: null });
        return Promise.resolve({ data: createdUnit });
      }
      return Promise.resolve({ data: {} });
    });
    render(<ControlDocumentIntegrityWorkspace />);

    fireEvent.click(screen.getByRole("button", { name: /Plant Explorer/ }));
    fireEvent.change(await screen.findByLabelText("Area or process unit name"), {
      target: { value: "New Area" },
    });
    fireEvent.change(screen.getByLabelText("Plant area"), {
      target: { value: "700A" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create area/process unit" }));

    await waitFor(() => {
      expect(mockAxios.post).toHaveBeenCalledWith(
        "http://api.test/api/process-units",
        { name: "New Area", area: "700A", description: null },
      );
    });
  });

  it("creates equipment under the selected area", async () => {
    const createdModule = { ...moduleRecord, id: "module-2", name: "Coat Prep" };
    mockAxios.post.mockImplementation((url, body) => {
      if (String(url).endsWith("/api/process-units/unit-1/modules")) {
        expect(body).toEqual({
          name: "Coat Prep",
          module_type: "Equipment Module",
          description: null,
          aliases: [],
          metadata_json: {},
        });
        return Promise.resolve({ data: createdModule });
      }
      return Promise.resolve({ data: {} });
    });
    render(<ControlDocumentIntegrityWorkspace />);

    fireEvent.click(screen.getByRole("button", { name: /Plant Explorer/ }));
    fireEvent.change(await screen.findByLabelText("Equipment name"), {
      target: { value: "Coat Prep" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add equipment" }));

    await waitFor(() => {
      expect(mockAxios.post).toHaveBeenCalledWith(
        "http://api.test/api/process-units/unit-1/modules",
        expect.objectContaining({ name: "Coat Prep" }),
      );
    });
  });

  it("renders the module record page with engineering records", async () => {
    render(<ControlDocumentIntegrityWorkspace />);

    fireEvent.click(screen.getByRole("button", { name: /Plant Explorer/ }));
    expect((await screen.findAllByText("Latest logic snapshot")).length).toBeGreaterThan(0);
    await waitFor(() => {
      expect(screen.getAllByText(/FILTRATE_SEPARATOR/).length).toBeGreaterThan(0);
    });
    expect(screen.getByText("Recommended next action")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: /Documents/ })[0]);
    expect((await screen.findAllByText("Documents")).length).toBeGreaterThan(0);
    expect(screen.getByText("Approved document exists. Generate a proposed revision when logic changes.")).toBeInTheDocument();
    expect(screen.getByText("No approved IO List exists.")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Generate first draft" }).length).toBeGreaterThan(0);
  });

  it("renders review items and proposed update details", async () => {
    render(<ControlDocumentIntegrityWorkspace />);

    await screen.findAllByText("Filtrate Separator");
    fireEvent.click(screen.getAllByRole("button", { name: /Review Queue/ })[0]);
    expect((await screen.findAllByText("Review Queue")).length).toBeGreaterThan(0);
    expect(screen.getByText("Equipment")).toBeInTheDocument();
    expect(screen.getByText("Document")).toBeInTheDocument();
    expect(screen.getByText("Reason")).toBeInTheDocument();
    expect(screen.getAllByText(/tags \+2 -0; routines \+1 -0/).length).toBeGreaterThan(0);
  });

  it("approve action calls the API", async () => {
    render(<ControlDocumentIntegrityWorkspace />);

    fireEvent.click(screen.getAllByRole("button", { name: /Review Queue/ })[0]);
    const approve = await screen.findByRole("button", { name: "Approve" });
    fireEvent.click(approve);

    await waitFor(() => {
      expect(mockAxios.post).toHaveBeenCalledWith(
        "http://api.test/api/review-items/review-1/approve",
        expect.objectContaining({ actor: "controls.engineer" }),
      );
    });
  });

  it("reject action calls the API", async () => {
    render(<ControlDocumentIntegrityWorkspace />);

    fireEvent.click(screen.getAllByRole("button", { name: /Review Queue/ })[0]);
    const reject = await screen.findByRole("button", { name: "Reject" });
    fireEvent.click(reject);

    await waitFor(() => {
      expect(mockAxios.post).toHaveBeenCalledWith(
        "http://api.test/api/review-items/review-1/reject",
        expect.objectContaining({ actor: "controls.engineer" }),
      );
    });
  });

  it("renders successful import summary and review workspace link", async () => {
    mockAxios.post.mockImplementation((url) => {
      if (String(url).endsWith("/api/import-sources")) {
        return Promise.resolve({ data: importSource });
      }
      if (String(url).endsWith("/api/import-sources/source-1/sync-upload")) {
        return Promise.resolve({ data: importResult });
      }
      return Promise.reject(new Error(`Unexpected POST ${String(url)}`));
    });
    render(<ControlDocumentIntegrityWorkspace />);

    fireEvent.click(screen.getByRole("button", { name: /Imports/ }));
    const input = await screen.findByLabelText("Export file");
    await screen.findAllByText("Filtrate Separator");
    fireEvent.change(input, {
      target: { files: [new File(["<xml />"], "FILTRATE_SEPARATOR.L5X")] },
    });
    fireEvent.click(screen.getByRole("button", { name: "Import Control Project" }));

    expect(await screen.findByText("Step 3 - Import Summary")).toBeInTheDocument();
    expect(screen.getByText(/Snapshot created/)).toBeInTheDocument();
    expect(screen.getByText(/Previous snapshot found/)).toBeInTheDocument();
    expect(screen.getByText(/Logic diff completed/)).toBeInTheDocument();
    expect(screen.getByText("Changes found")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Review Workspace" })).toHaveAttribute(
      "href",
      "/workspace/integrity?unitId=unit-1&moduleId=module-1",
    );
  });

  it("selects a module from URL query params", async () => {
    resetNavigationMocks("unitId=unit-1&moduleId=module-1");
    render(<ControlDocumentIntegrityWorkspace />);

    fireEvent.click(screen.getByRole("button", { name: /Plant Explorer/ }));
    await screen.findAllByText("Latest logic snapshot");
    expect(mockAxios.get).toHaveBeenCalledWith("http://api.test/api/modules/module-1/record");
  });

  it("clicking Generate Draft calls API and refreshes the document card", async () => {
    let draftCreated = false;
    let resolveDraft!: (value: { data: typeof draftRevision }) => void;
    const draftPromise = new Promise<{ data: typeof draftRevision }>((resolve) => {
      resolveDraft = resolve;
    });
    mockAxios.get.mockImplementation((url) => {
      const target = String(url);
      if (target.endsWith("/api/engineering-records")) {
        return Promise.resolve({
          data: draftCreated ? [engineeringRecord, ioRecord] : [engineeringRecord],
        });
      }
      if (target.endsWith("/api/engineering-records/record-2/revisions")) {
        return Promise.resolve({ data: [draftRevision] });
      }
      if (target.endsWith("/api/process-units/unit-1/review-items")) {
        return Promise.resolve({
          data: draftCreated
            ? [
                reviewItem,
                {
                  ...reviewItem,
                  id: "review-2",
                  engineering_record_id: "record-2",
                  proposed_document_update_id: null,
                  title: "Review draft Filtrate Separator IO List",
                  reason: "Deterministic draft generated for IO List.",
                },
              ]
            : [reviewItem],
        });
      }
      return mockGet(target);
    });
    mockAxios.post.mockImplementation((url) => {
      if (
        String(url).endsWith(
          "/api/modules/module-1/engineering-records/io_list/generate-draft",
        )
      ) {
        return draftPromise;
      }
      return Promise.resolve({ data: {} });
    });

    render(<ControlDocumentIntegrityWorkspace />);

    fireEvent.click(screen.getAllByRole("button", { name: /Documents/ })[0]);
    await screen.findByText("Filtrate Separator control narrative");
    const generate = await screen.findAllByRole("button", { name: "Generate first draft" });
    fireEvent.click(generate[0]);

    expect(await screen.findByRole("button", { name: "Generating..." })).toBeInTheDocument();
    draftCreated = true;
    resolveDraft({ data: draftRevision });

    await waitFor(() => {
      expect(mockAxios.post).toHaveBeenCalledWith(
        "http://api.test/api/modules/module-1/engineering-records/io_list/generate-draft",
        null,
        { params: { actor: "controls.engineer" } },
      );
    });
    expect(await screen.findByText("IO List draft created. Review required.")).toBeInTheDocument();
    expect(await screen.findByText("Filtrate Separator IO List")).toBeInTheDocument();
    expect(screen.getAllByText("Draft").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "Review Draft" }));
    expect((await screen.findAllByText("Review Queue")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Equipment").length).toBeGreaterThan(0);
    expect(screen.getByText("Deterministic draft generated for IO List.")).toBeInTheDocument();
    const openReviewButtons = screen.getAllByRole("button", { name: "Open Review ->" });
    fireEvent.click(openReviewButtons[1]);
    expect(await screen.findByText("Generated document draft")).toBeInTheDocument();
    expect(screen.getAllByText(/MIX_TIMER/).length).toBeGreaterThan(0);
  });

  it("renders the Generate Document panel", async () => {
    render(<ControlDocumentIntegrityWorkspace />);

    fireEvent.click(screen.getAllByRole("button", { name: /Documents/ })[0]);

    expect(await screen.findByRole("heading", { name: "Generate Document" })).toBeInTheDocument();
    expect(screen.getByText(/safe disabled or fake mode/)).toBeInTheDocument();
    expect(screen.getByLabelText("Control Narrative")).toBeChecked();
    expect(screen.getByLabelText("IO List")).not.toBeChecked();
    expect(screen.getByLabelText("User notes")).toBeInTheDocument();
  });

  it("generation mode selector works", async () => {
    render(<ControlDocumentIntegrityWorkspace />);

    fireEvent.click(screen.getAllByRole("button", { name: /Documents/ })[0]);
    expect(await screen.findByLabelText("AI-assisted")).toBeChecked();
    fireEvent.click(screen.getByLabelText("Deterministic"));
    expect(screen.getByLabelText("Deterministic")).toBeChecked();
    expect(screen.getByLabelText("AI-assisted")).not.toBeChecked();
  });

  it("generates only selected document types through the AI draft endpoint", async () => {
    mockAxios.post.mockImplementation((url) => {
      if (
        String(url).endsWith(
          "/api/modules/module-1/engineering-records/cause_effect_matrix/generate-ai-draft",
        )
      ) {
        return Promise.resolve({
          data: {
            ...aiDraftResponse,
            revision: { ...draftRevision, id: "revision-cem" },
            generation_mode: "deterministic_template",
          },
        });
      }
      return Promise.resolve({ data: {} });
    });
    render(<ControlDocumentIntegrityWorkspace />);

    fireEvent.click(screen.getAllByRole("button", { name: /Documents/ })[0]);
    await screen.findByText(/Generate only the engineering documents you choose for/);
    fireEvent.click(screen.getByLabelText("Deterministic"));
    fireEvent.click(screen.getByLabelText("Control Narrative"));
    fireEvent.click(screen.getByLabelText("Cause & Effect Matrix"));
    fireEvent.click(screen.getByRole("button", { name: "Generate 1 selected document draft" }));

    await waitFor(() => {
      expect(mockAxios.post).toHaveBeenCalledWith(
        "http://api.test/api/modules/module-1/engineering-records/cause_effect_matrix/generate-ai-draft",
        {
          generation_mode: "deterministic_template",
          user_notes: null,
          actor: "controls.engineer",
        },
      );
    });
    expect(mockAxios.post).not.toHaveBeenCalledWith(
      "http://api.test/api/modules/module-1/engineering-records/io_list/generate-ai-draft",
      expect.anything(),
    );
  });

  it("sends user notes in the AI draft request", async () => {
    mockAxios.post.mockResolvedValue({ data: aiDraftResponse });
    render(<ControlDocumentIntegrityWorkspace />);

    fireEvent.click(screen.getAllByRole("button", { name: /Documents/ })[0]);
    await screen.findByRole("heading", { name: "Generate Document" });
    fireEvent.change(screen.getByLabelText("User notes"), {
      target: { value: "Focus on startup sequence." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Generate 1 selected document draft" }));

    await waitFor(() => {
      expect(mockAxios.post).toHaveBeenCalledWith(
        "http://api.test/api/modules/module-1/engineering-records/control_narrative/generate-ai-draft",
        {
          generation_mode: "llm_assisted",
          user_notes: "Focus on startup sequence.",
          actor: "controls.engineer",
        },
      );
    });
  });

  it("multiple selected documents trigger multiple AI draft calls", async () => {
    mockAxios.post.mockResolvedValue({ data: aiDraftResponse });
    render(<ControlDocumentIntegrityWorkspace />);

    fireEvent.click(screen.getAllByRole("button", { name: /Documents/ })[0]);
    await screen.findByRole("heading", { name: "Generate Document" });
    fireEvent.click(screen.getByLabelText("IO List"));
    fireEvent.click(screen.getByRole("button", { name: "Generate 2 selected document drafts" }));

    await waitFor(() => {
      expect(mockAxios.post).toHaveBeenCalledWith(
        "http://api.test/api/modules/module-1/engineering-records/control_narrative/generate-ai-draft",
        expect.objectContaining({ generation_mode: "llm_assisted" }),
      );
      expect(mockAxios.post).toHaveBeenCalledWith(
        "http://api.test/api/modules/module-1/engineering-records/io_list/generate-ai-draft",
        expect.objectContaining({ generation_mode: "llm_assisted" }),
      );
    });
  });

  it("success summary renders warnings, missing facts, and confidence", async () => {
    mockAxios.post.mockResolvedValue({ data: aiDraftResponse });
    render(<ControlDocumentIntegrityWorkspace />);

    fireEvent.click(screen.getAllByRole("button", { name: /Documents/ })[0]);
    await screen.findByRole("heading", { name: "Generate Document" });
    fireEvent.click(screen.getByRole("button", { name: "Generate 1 selected document draft" }));

    expect(await screen.findByText("Generated draft summary")).toBeInTheDocument();
    expect(screen.getByText("Confidence: medium")).toBeInTheDocument();
    expect(screen.getByText("llm_assist_disabled")).toBeInTheDocument();
    expect(screen.getByText("Setpoints: Needs engineer input")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open Draft for Review" }));
    expect((await screen.findAllByText("Review Queue")).length).toBeGreaterThan(0);
  });

  it("renders an error when AI draft generation fails", async () => {
    mockAxios.post.mockImplementation((url) => {
      if (
        String(url).endsWith(
          "/api/modules/module-1/engineering-records/control_narrative/generate-ai-draft",
        )
      ) {
        return Promise.reject(new Error("ai generation failed"));
      }
      return Promise.resolve({ data: {} });
    });
    render(<ControlDocumentIntegrityWorkspace />);

    fireEvent.click(screen.getAllByRole("button", { name: /Documents/ })[0]);
    await screen.findByRole("heading", { name: "Generate Document" });
    fireEvent.click(screen.getByRole("button", { name: "Generate 1 selected document draft" }));

    expect(await screen.findByText("Unable to generate selected AI document drafts")).toBeInTheDocument();
  });

  it("reset generated data asks for confirmation", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    mockAxios.post.mockImplementation((url) => {
      if (String(url).endsWith("/api/control-integrity/reset-generated")) {
        return Promise.resolve({ data: { revisions_deleted: 2, reviews_deleted: 3 } });
      }
      return Promise.resolve({ data: {} });
    });
    render(<ControlDocumentIntegrityWorkspace />);

    fireEvent.click(screen.getByRole("button", { name: /Plant Explorer/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Reset generated drafts/reviews" }));

    expect(confirm).toHaveBeenCalledWith(
      "Reset generated drafts, rejected drafts, and open reviews? Approved revisions are preserved.",
    );
    await waitFor(() => {
      expect(mockAxios.post).toHaveBeenCalledWith("http://api.test/api/control-integrity/reset-generated");
    });
    confirm.mockRestore();
  });

  it("delete draft revision asks for confirmation", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    mockAxios.get.mockImplementation((url) => {
      const target = String(url);
      if (target.endsWith("/api/engineering-records")) {
        return Promise.resolve({ data: [engineeringRecord, ioRecord] });
      }
      if (target.endsWith("/api/engineering-records/record-2/revisions")) {
        return Promise.resolve({ data: [draftRevision] });
      }
      return mockGet(target);
    });
    mockAxios.delete.mockResolvedValue({ data: { deleted: true } });
    render(<ControlDocumentIntegrityWorkspace />);

    fireEvent.click(screen.getAllByRole("button", { name: /Documents/ })[0]);
    const deleteDraft = await screen.findByRole("button", { name: "Delete draft" });
    fireEvent.click(deleteDraft);

    expect(confirm).toHaveBeenCalledWith("Delete revision 0.1? This only removes draft or rejected work.");
    await waitFor(() => {
      expect(mockAxios.delete).toHaveBeenCalledWith("http://api.test/api/document-revisions/revision-2");
    });
    confirm.mockRestore();
  });

  it("clicking Generate Proposed Revision calls API", async () => {
    render(<ControlDocumentIntegrityWorkspace />);

    fireEvent.click(screen.getAllByRole("button", { name: /Documents/ })[0]);
    const generate = await screen.findByRole("button", { name: "Generate Proposed Revision" });
    fireEvent.click(generate);

    await waitFor(() => {
      expect(mockAxios.post).toHaveBeenCalledWith(
        "http://api.test/api/engineering-records/record-1/generate-proposed-revision",
        null,
        { params: { actor: "controls.engineer" } },
      );
    });
  });

  it("preserves selected module after draft generation refresh", async () => {
    const moduleTwo = { ...moduleRecord, id: "module-2", name: "Coat Prep" };
    const getCalls: string[] = [];
    mockAxios.get.mockImplementation((url, config) => {
      const target = String(url);
      getCalls.push(target);
      if (target.endsWith("/api/process-units/unit-1/modules")) {
        return Promise.resolve({ data: [moduleRecord, moduleTwo] });
      }
      if (target.endsWith("/api/modules/module-2/record")) {
        return Promise.resolve({
          data: {
            module: moduleTwo,
            snapshots: [snapshot],
            narratives: [],
            governance_artifacts: [],
            issues: [],
          },
        });
      }
      if (target.endsWith("/api/engineering-records")) {
        const moduleId = (config as { params?: { module_id?: string } })?.params?.module_id;
        if (moduleId === "module-2") {
          return Promise.resolve({ data: [] });
        }
        return Promise.resolve({ data: [engineeringRecord] });
      }
      if (target.endsWith("/api/process-units/unit-1/review-items")) {
        return Promise.resolve({ data: [] });
      }
      return mockGet(target);
    });
    mockAxios.post.mockImplementation((url) => {
      if (
        String(url).endsWith(
          "/api/modules/module-2/engineering-records/io_list/generate-draft",
        )
      ) {
        return Promise.resolve({ data: draftRevision });
      }
      return Promise.resolve({ data: {} });
    });

    render(<ControlDocumentIntegrityWorkspace />);

    fireEvent.click(screen.getByRole("button", { name: /Plant Explorer/ }));
    await screen.findByText("Coat Prep");
    fireEvent.click(screen.getByRole("button", { name: "Coat Prep" }));
    fireEvent.click(screen.getAllByRole("button", { name: /Documents/ })[0]);
    const generate = await screen.findAllByRole("button", { name: "Generate first draft" });
    const getCallsBeforeGenerate = getCalls.length;
    fireEvent.click(generate[0]);

    await waitFor(() => {
      expect(mockAxios.post).toHaveBeenCalledWith(
        "http://api.test/api/modules/module-2/engineering-records/io_list/generate-draft",
        null,
        { params: { actor: "controls.engineer" } },
      );
    });
    await waitFor(() => {
      const callsAfterGenerate = getCalls.slice(getCallsBeforeGenerate);
      expect(
        callsAfterGenerate.filter((url) => url.endsWith("/api/modules/module-2/record")).length,
      ).toBeGreaterThan(0);
      expect(
        callsAfterGenerate.filter((url) => url.endsWith("/api/modules/module-1/record")).length,
      ).toBe(0);
    });
  });

  it("renders an error when draft generation fails", async () => {
    mockAxios.post.mockImplementation((url) => {
      if (
        String(url).endsWith(
          "/api/modules/module-1/engineering-records/io_list/generate-draft",
        )
      ) {
        return Promise.reject(new Error("generation failed"));
      }
      return Promise.resolve({ data: {} });
    });
    render(<ControlDocumentIntegrityWorkspace />);

    fireEvent.click(screen.getAllByRole("button", { name: /Documents/ })[0]);
    await screen.findByText("Filtrate Separator control narrative");
    const generate = await screen.findAllByRole("button", { name: "Generate first draft" });
    fireEvent.click(generate[0]);

    expect(await screen.findByText("Unable to generate IO List draft")).toBeInTheDocument();
  });
});
