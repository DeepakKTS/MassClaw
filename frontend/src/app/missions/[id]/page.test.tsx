import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import MissionDetailPage from "./page";
import { api } from "@/lib/api";
import {
  makeTask,
  makeWorkflow,
  makeWorkflowStatus,
  renderWithProviders,
} from "@/test/utils";

vi.mock("@/lib/api", () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn(), getRaw: vi.fn() },
}));

// Overrides the blanket next/navigation stub in vitest.setup.ts: this route
// reads the workflow id straight out of the URL params.
const params = { id: "wf-detail" };
vi.mock("next/navigation", () => ({
  useParams: () => params,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/missions/wf-detail",
  useSearchParams: () => new URLSearchParams(),
}));

// agent-plan pulls a heavy animated task tree that is exercised by its own
// component tests; the route smoke test only needs to know it mounted.
vi.mock("@/components/ui/agent-plan", () => ({
  default: () => <div data-testid="agent-plan" />,
}));

const apiGet = vi.mocked(api.get);

/** The route fans out to four endpoints in parallel. Routing by path keeps
 *  each test's intent readable instead of depending on call order. */
function routeApi(overrides: Record<string, unknown> = {}) {
  const responses: Record<string, unknown> = {
    "/workflows/wf-detail": makeWorkflow({ workflow_id: "wf-detail" }),
    "/workflows/wf-detail/status": makeWorkflowStatus({ workflow_id: "wf-detail" }),
    "/tasks/workflow/wf-detail": [makeTask({ workflow_id: "wf-detail" })],
    "/wallet/workflow/wf-detail/balance": {
      workflow_id: "wf-detail",
      balance: 57.5,
      budget_limit: 100,
      budget_used: 42.5,
    },
    ...overrides,
  };

  apiGet.mockImplementation((path: string) => {
    if (!(path in responses)) {
      return Promise.reject(new Error(`unexpected request: ${path}`));
    }
    const value = responses[path];
    return value instanceof Error ? Promise.reject(value) : Promise.resolve(value as never);
  });
}

beforeEach(() => {
  routeApi();
});

describe("/missions/[id]", () => {
  it("shows a spinner until the workflow resolves", () => {
    apiGet.mockReturnValue(new Promise(() => {}));
    const { container } = renderWithProviders(<MissionDetailPage />);
    expect(container.querySelector(".animate-spin")).toBeTruthy();
  });

  it("fetches the workflow, its status, its tasks and its wallet balance", async () => {
    renderWithProviders(<MissionDetailPage />);
    await screen.findByTestId("agent-plan");

    expect(apiGet).toHaveBeenCalledWith("/workflows/wf-detail");
    expect(apiGet).toHaveBeenCalledWith("/workflows/wf-detail/status");
    expect(apiGet).toHaveBeenCalledWith("/tasks/workflow/wf-detail");
    expect(apiGet).toHaveBeenCalledWith("/wallet/workflow/wf-detail/balance");
  });

  it("renders the prompt and a link back to Mission Control", async () => {
    renderWithProviders(<MissionDetailPage />);
    // The prompt renders in both the header and the summary block.
    expect((await screen.findAllByText(/Research the ISS crew rotation schedule/)).length)
      .toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: /Mission Control/ })).toHaveAttribute("href", "/");
  });

  it("renders the not-found state when the workflow is missing", async () => {
    routeApi({ "/workflows/wf-detail": null });
    renderWithProviders(<MissionDetailPage />);
    expect(await screen.findByText("Mission not found")).toBeInTheDocument();
  });

  it("renders a running mission without a result payload", async () => {
    routeApi({
      "/workflows/wf-detail": makeWorkflow({
        workflow_id: "wf-detail",
        status: "running",
        result: null,
        completed_at: null,
      }),
      "/workflows/wf-detail/status": makeWorkflowStatus({
        workflow_id: "wf-detail",
        status: "running",
        progress_percent: 33,
        completed_tasks: 1,
        running_tasks: 1,
      }),
    });
    renderWithProviders(<MissionDetailPage />);
    // A null `result` is the common in-flight case and must not throw.
    expect(await screen.findByTestId("agent-plan")).toBeInTheDocument();
  });

  it("renders when the status and wallet calls fail but the workflow loads", async () => {
    routeApi({
      "/workflows/wf-detail/status": new Error("status unavailable"),
      "/wallet/workflow/wf-detail/balance": new Error("wallet unavailable"),
    });
    renderWithProviders(<MissionDetailPage />);
    // The page treats these as optional enrichments; losing them must
    // degrade the view, not blank it.
    expect((await screen.findAllByText(/Research the ISS crew rotation schedule/)).length)
      .toBeGreaterThan(0);
  });
});
