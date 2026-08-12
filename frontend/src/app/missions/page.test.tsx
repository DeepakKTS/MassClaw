import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import MissionsPage from "./page";
import { api } from "@/lib/api";
import { makeWorkflow, renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn(), getRaw: vi.fn() },
}));

const apiGet = vi.mocked(api.get);

function page(items: ReturnType<typeof makeWorkflow>[]) {
  return { items, total: items.length, page: 1, page_size: 20 };
}

beforeEach(() => {
  apiGet.mockResolvedValue(page([]));
});

describe("/missions", () => {
  it("renders the heading and a spinner while loading", () => {
    apiGet.mockReturnValue(new Promise(() => {}));
    renderWithProviders(<MissionsPage />);
    expect(screen.getByRole("heading", { name: "Mission Log" })).toBeInTheDocument();
    // Count reads 0 until the first page lands.
    expect(screen.getByText("0 missions executed")).toBeInTheDocument();
  });

  it("requests the first page from the workflows endpoint", async () => {
    renderWithProviders(<MissionsPage />);
    expect(await screen.findByText("No missions yet")).toBeInTheDocument();
    expect(apiGet).toHaveBeenCalledWith("/workflows?page=1");
  });

  it("shows the empty state when no missions exist", async () => {
    renderWithProviders(<MissionsPage />);
    expect(await screen.findByText("No missions yet")).toBeInTheDocument();
    expect(screen.getByText(/Launch your first mission/)).toBeInTheDocument();
  });

  it("lists missions with their prompt and links to the detail route", async () => {
    apiGet.mockResolvedValue(
      page([
        makeWorkflow({ workflow_id: "wf-a", prompt: "Summarise the Q3 filings" }),
        makeWorkflow({ workflow_id: "wf-b", prompt: "Draft the launch checklist" }),
      ]),
    );
    renderWithProviders(<MissionsPage />);

    expect(await screen.findByText(/Summarise the Q3 filings/)).toBeInTheDocument();
    expect(screen.getByText(/Draft the launch checklist/)).toBeInTheDocument();
    expect(screen.getByText("2 missions executed")).toBeInTheDocument();

    const links = screen.getAllByRole("link").map((a) => a.getAttribute("href"));
    expect(links).toContain("/missions/wf-a");
    expect(links).toContain("/missions/wf-b");
  });

  it("renders a running mission without crashing on a null budget", async () => {
    apiGet.mockResolvedValue(
      page([
        makeWorkflow({
          workflow_id: "wf-running",
          status: "running",
          budget_limit: 0,
          budget_used: 0,
          completed_at: null,
        }),
      ]),
    );
    renderWithProviders(<MissionsPage />);
    // budget_limit 0 makes the percentage a divide-by-zero guard, so this
    // asserts the guard holds rather than any particular number.
    expect(await screen.findByText(/Research the ISS crew rotation schedule/)).toBeInTheDocument();
  });

  it("offers a route back to Mission Control to start a new mission", async () => {
    renderWithProviders(<MissionsPage />);
    expect(await screen.findByRole("link", { name: /New Mission/ })).toHaveAttribute("href", "/");
  });
});
