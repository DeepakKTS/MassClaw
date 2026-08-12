import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ApprovalsPage from "./page";
import { api } from "@/lib/api";
import { makeApproval, renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn(), getRaw: vi.fn() },
}));

const apiGet = vi.mocked(api.get);
const apiPost = vi.mocked(api.post);

/** The page mounts the SSE hook on render, so jsdom needs an EventSource
 *  even for the cases that never exercise the stream.
 *
 *  Note it collects *every* instance: this page opens two streams, because
 *  it calls useApprovalsStream() itself and usePendingApprovals() calls it
 *  again internally. The badge is driven by the page's own instance, so a
 *  test that only opened the most recent one would never see it flip. */
class StubEventSource {
  static instances: StubEventSource[] = [];
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {
    StubEventSource.instances.push(this);
  }
  addEventListener() {}
  removeEventListener() {}
  close() {}
}

function openAllStreams() {
  act(() => {
    StubEventSource.instances.forEach((source) => source.onopen?.());
  });
}

beforeEach(() => {
  StubEventSource.instances = [];
  vi.stubGlobal("EventSource", StubEventSource);
  apiGet.mockResolvedValue([]);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("/approvals", () => {
  it("renders the header and a loading state before data arrives", () => {
    apiGet.mockReturnValue(new Promise(() => {}));
    renderWithProviders(<ApprovalsPage />);

    expect(screen.getByRole("heading", { name: "Human Approvals" })).toBeInTheDocument();
    expect(screen.getByText("Loading approvals…")).toBeInTheDocument();
  });

  it("shows the all-clear empty state when nothing is pending", async () => {
    renderWithProviders(<ApprovalsPage />);
    expect(await screen.findByText("All clear")).toBeInTheDocument();
    expect(screen.getByText(/No pending approvals/)).toBeInTheDocument();
  });

  it("starts in the reconnecting state and flips to live when the stream opens", async () => {
    renderWithProviders(<ApprovalsPage />);
    expect(screen.getByText("Reconnecting…")).toBeInTheDocument();

    openAllStreams();
    expect(await screen.findByText("Live (SSE)")).toBeInTheDocument();
    expect(screen.queryByText("Reconnecting…")).not.toBeInTheDocument();
  });

  it("drops back to reconnecting when the stream errors", async () => {
    renderWithProviders(<ApprovalsPage />);
    openAllStreams();
    expect(await screen.findByText("Live (SSE)")).toBeInTheDocument();

    act(() => {
      StubEventSource.instances.forEach((source) => source.onerror?.());
    });
    expect(await screen.findByText("Reconnecting…")).toBeInTheDocument();
  });

  it("opens one stream per useApprovalsStream caller on this page", () => {
    renderWithProviders(<ApprovalsPage />);
    // Currently two: the page's own call plus the one inside
    // usePendingApprovals. Both point at the same endpoint, so this is a
    // duplicated connection rather than intended fan-out. Pinned so that
    // de-duplicating it is a deliberate, visible change.
    expect(StubEventSource.instances).toHaveLength(2);
    expect(new Set(StubEventSource.instances.map((s) => s.url))).toEqual(
      new Set(["/api/v1/approvals/stream"]),
    );
  });

  it("renders a pending approval's action, capability, rule and confidence", async () => {
    apiGet.mockResolvedValue([makeApproval()]);
    renderWithProviders(<ApprovalsPage />);

    // The action is de-underscored for display.
    expect(await screen.findByText("send email")).toBeInTheDocument();
    expect(screen.getByText("email_send")).toBeInTheDocument();
    expect(screen.getByText("require_approval_for_external_send")).toBeInTheDocument();
    expect(screen.getByText("Agent: comms-agent")).toBeInTheDocument();
    expect(screen.getByText("31.0%")).toBeInTheDocument();
    expect(screen.getByText("Pending")).toBeInTheDocument();
    expect(screen.getByText("1 pending approval")).toBeInTheDocument();
  });

  it("pluralises the pending count", async () => {
    apiGet.mockResolvedValue([
      makeApproval(),
      makeApproval({ request_id: "apr-second", action: "wire_transfer" }),
    ]);
    renderWithProviders(<ApprovalsPage />);
    expect(await screen.findByText("2 pending approvals")).toBeInTheDocument();
  });

  it("exposes the checkpoint hash as the resume pointer", async () => {
    apiGet.mockResolvedValue([makeApproval()]);
    renderWithProviders(<ApprovalsPage />);

    // This is what an operator pastes into POST /workflows/{id}/resume, so
    // it must render in full rather than truncated.
    expect(await screen.findByText("c0ffee1234567890abcdef")).toBeInTheDocument();
    expect(screen.getByText(/Resume Pointer/)).toBeInTheDocument();
  });

  it("requires a second click to confirm an approval", async () => {
    const user = userEvent.setup();
    apiGet.mockResolvedValue([makeApproval()]);
    apiPost.mockResolvedValue({ status: "approved" });
    renderWithProviders(<ApprovalsPage />);

    const approve = await screen.findByRole("button", { name: "Approve" });
    await user.click(approve);

    // First click only arms the confirmation — nothing is sent yet.
    expect(apiPost).not.toHaveBeenCalled();
    const confirm = await screen.findByRole("button", { name: "Confirm Approve" });
    expect(screen.getByPlaceholderText(/Reason for approve/)).toBeInTheDocument();

    await user.click(confirm);
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/approvals/apr-00000000-0000-0000-0000-0000000000aa/approve", {
        reason: "",
        decided_by: "human",
      }),
    );
  });

  it("sends the typed reason with a denial", async () => {
    const user = userEvent.setup();
    apiGet.mockResolvedValue([makeApproval()]);
    apiPost.mockResolvedValue({ status: "denied" });
    renderWithProviders(<ApprovalsPage />);

    await user.click(await screen.findByRole("button", { name: "Deny" }));
    await user.type(screen.getByPlaceholderText(/Reason for deny/), "recipient unverified");
    await user.click(screen.getByRole("button", { name: "Confirm Deny" }));

    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith(expect.stringContaining("/deny"), {
        reason: "recipient unverified",
        decided_by: "human",
      }),
    );
  });

  it("cancel disarms the confirmation without sending anything", async () => {
    const user = userEvent.setup();
    apiGet.mockResolvedValue([makeApproval()]);
    renderWithProviders(<ApprovalsPage />);

    await user.click(await screen.findByRole("button", { name: "Approve" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(apiPost).not.toHaveBeenCalled();
    expect(await screen.findByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/Reason for/)).not.toBeInTheDocument();
  });

  it("hides the decision controls for an already-decided request", async () => {
    apiGet.mockResolvedValue([
      makeApproval({
        status: "approved",
        decided_by: "deepak",
        decided_at: "2026-08-12T10:03:00Z",
      }),
    ]);
    renderWithProviders(<ApprovalsPage />);

    expect(await screen.findByText("Approved")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.getByText(/Decided by/)).toBeInTheDocument();
  });

  it("renders an unrecognised status through the fallback badge", async () => {
    apiGet.mockResolvedValue([makeApproval({ status: "expired" })]);
    renderWithProviders(<ApprovalsPage />);
    expect(await screen.findByText("expired")).toBeInTheDocument();
  });

  it("surfaces a fetch failure inline", async () => {
    apiGet.mockRejectedValue(new Error("Bad Gateway"));
    renderWithProviders(<ApprovalsPage />);

    expect(await screen.findByText(/Failed to load approvals: Bad Gateway/)).toBeInTheDocument();
    // The empty state must not double up with the error state.
    expect(screen.queryByText("All clear")).not.toBeInTheDocument();
  });
});
