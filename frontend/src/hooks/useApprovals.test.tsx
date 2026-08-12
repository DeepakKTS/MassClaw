import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  useApprovalsStream,
  useApproveRequest,
  useDenyRequest,
  usePendingApprovals,
} from "./useApprovals";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn(), getRaw: vi.fn() },
}));

const apiGet = vi.mocked(api.get);
const apiPost = vi.mocked(api.post);

/** jsdom ships no EventSource, so the hook's entire transport is stubbed.
 *  Instances register themselves so tests can drive open/error/events and
 *  assert on close(). */
class FakeEventSource {
  static instances: FakeEventSource[] = [];

  static reset() {
    FakeEventSource.instances = [];
  }

  static get last() {
    const source = FakeEventSource.instances.at(-1);
    if (!source) throw new Error("no EventSource was constructed");
    return source;
  }

  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  readonly listeners = new Map<string, Set<() => void>>();

  constructor(public readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, handler: () => void) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(handler);
  }

  removeEventListener(type: string, handler: () => void) {
    this.listeners.get(type)?.delete(handler);
  }

  close() {
    this.closed = true;
  }

  /** Test driver: fire a named server event. */
  emit(type: string) {
    this.listeners.get(type)?.forEach((handler) => handler());
  }

  open() {
    this.onopen?.();
  }

  fail() {
    this.onerror?.();
  }
}

function wrapper(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

function newClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
}

beforeEach(() => {
  FakeEventSource.reset();
  vi.stubGlobal("EventSource", FakeEventSource);
  apiGet.mockResolvedValue([]);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("useApprovalsStream", () => {
  it("opens a stream against the prefixed SSE route", () => {
    renderHook(() => useApprovalsStream(), { wrapper: wrapper(newClient()) });
    // This is the exact path the backend mounts; a prefix regression here
    // is what the mount-integrity guard covers on the server side.
    expect(FakeEventSource.last.url).toBe("/api/v1/approvals/stream");
  });

  it("reports live only after the connection opens", async () => {
    const { result } = renderHook(() => useApprovalsStream(), { wrapper: wrapper(newClient()) });
    expect(result.current.live).toBe(false);

    act(() => FakeEventSource.last.open());
    await waitFor(() => expect(result.current.live).toBe(true));
  });

  it("invalidates the approvals cache on every approval lifecycle event", () => {
    const client = newClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    renderHook(() => useApprovalsStream(), { wrapper: wrapper(client) });

    const events = [
      "connected",
      "approval.requested",
      "approval.approved",
      "approval.denied",
      "approval.expired",
    ];
    for (const event of events) {
      invalidate.mockClear();
      act(() => FakeEventSource.last.emit(event));
      expect(invalidate, `${event} should refresh the cache`).toHaveBeenCalledWith({
        queryKey: ["approvals"],
      });
    }
  });

  it("ignores unrelated event types", () => {
    const client = newClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    renderHook(() => useApprovalsStream(), { wrapper: wrapper(client) });

    act(() => FakeEventSource.last.emit("heartbeat"));
    expect(invalidate).not.toHaveBeenCalled();
  });

  it("drops live, closes the dead source and reconnects with backoff", async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useApprovalsStream(), { wrapper: wrapper(newClient()) });

    act(() => FakeEventSource.last.open());
    expect(result.current.live).toBe(true);

    const first = FakeEventSource.last;
    act(() => first.fail());
    expect(result.current.live).toBe(false);
    expect(first.closed).toBe(true);
    expect(FakeEventSource.instances).toHaveLength(1);

    // First retry: 1_000 * 2**1 = 2s.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_999);
    });
    expect(FakeEventSource.instances).toHaveLength(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(FakeEventSource.instances).toHaveLength(2);
  });

  it("caps the backoff at 30s after repeated failures", async () => {
    vi.useFakeTimers();
    renderHook(() => useApprovalsStream(), { wrapper: wrapper(newClient()) });

    // retryRef is clamped at 5, so the delay saturates at
    // min(30_000, 1_000 * 2**5) = 30_000 and never grows past it.
    for (let attempt = 1; attempt <= 7; attempt += 1) {
      const expected = Math.min(30_000, 1_000 * 2 ** Math.min(attempt, 5));
      const before = FakeEventSource.instances.length;
      act(() => FakeEventSource.last.fail());

      await act(async () => {
        await vi.advanceTimersByTimeAsync(expected - 1);
      });
      expect(
        FakeEventSource.instances.length,
        `attempt ${attempt} should still be waiting at ${expected - 1}ms`,
      ).toBe(before);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1);
      });
      expect(FakeEventSource.instances.length).toBe(before + 1);
    }
  });

  it("resets the backoff counter once a reconnect succeeds", async () => {
    vi.useFakeTimers();
    renderHook(() => useApprovalsStream(), { wrapper: wrapper(newClient()) });

    // Fail twice to push the delay to 4s, then succeed.
    act(() => FakeEventSource.last.fail());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    act(() => FakeEventSource.last.fail());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4_000);
    });
    act(() => FakeEventSource.last.open());

    // Back to the 2s first-retry delay rather than 8s.
    const before = FakeEventSource.instances.length;
    act(() => FakeEventSource.last.fail());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(FakeEventSource.instances.length).toBe(before + 1);
  });

  it("closes the stream and cancels a pending reconnect on unmount", async () => {
    vi.useFakeTimers();
    const { result, unmount } = renderHook(() => useApprovalsStream(), {
      wrapper: wrapper(newClient()),
    });

    act(() => FakeEventSource.last.open());
    expect(result.current.live).toBe(true);

    const source = FakeEventSource.last;
    unmount();
    expect(source.closed).toBe(true);

    // A reconnect scheduled before unmount must not resurrect the stream.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(FakeEventSource.instances).toHaveLength(1);
  });

  it("no-ops when the environment has no EventSource", () => {
    vi.stubGlobal("EventSource", undefined);
    const { result } = renderHook(() => useApprovalsStream(), { wrapper: wrapper(newClient()) });
    expect(result.current.live).toBe(false);
  });
});

describe("usePendingApprovals", () => {
  it("fetches the pending list from the approvals endpoint", async () => {
    apiGet.mockResolvedValue([{ request_id: "a1", status: "pending" }]);
    const { result } = renderHook(() => usePendingApprovals(), { wrapper: wrapper(newClient()) });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(apiGet).toHaveBeenCalledWith("/approvals/pending");
    expect(result.current.data).toEqual([{ request_id: "a1", status: "pending" }]);
  });

  // RTL's waitFor polls on real timers, so these two drive the initial
  // fetch through the fake clock instead of waiting on it.
  it("polls on the 30s fallback while the stream is down", async () => {
    vi.useFakeTimers();
    renderHook(() => usePendingApprovals(), { wrapper: wrapper(newClient()) });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(apiGet).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(apiGet).toHaveBeenCalledTimes(2);
  });

  it("stops polling once the stream reports live", async () => {
    vi.useFakeTimers();
    renderHook(() => usePendingApprovals(), { wrapper: wrapper(newClient()) });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    act(() => FakeEventSource.last.open());
    const callsWhenLive = apiGet.mock.calls.length;

    // Two full fallback windows with the stream up: no extra polling. The
    // stream's cache invalidation is what refreshes the list instead.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(apiGet).toHaveBeenCalledTimes(callsWhenLive);
  });

  it("surfaces a failed fetch as an error state", async () => {
    apiGet.mockRejectedValue(new Error("Bad Gateway"));
    const { result } = renderHook(() => usePendingApprovals(), { wrapper: wrapper(newClient()) });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect((result.current.error as Error).message).toBe("Bad Gateway");
  });
});

describe("approve and deny mutations", () => {
  it("posts an approval and refreshes the cache", async () => {
    apiPost.mockResolvedValue({ status: "approved" });
    const client = newClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useApproveRequest(), { wrapper: wrapper(client) });

    await act(async () => {
      await result.current.mutateAsync({ id: "a1", reason: "verified", decided_by: "deepak" });
    });

    expect(apiPost).toHaveBeenCalledWith("/approvals/a1/approve", {
      reason: "verified",
      decided_by: "deepak",
    });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["approvals"] });
  });

  it("defaults an omitted reason to empty and the decider to human", async () => {
    apiPost.mockResolvedValue({ status: "denied" });
    const { result } = renderHook(() => useDenyRequest(), { wrapper: wrapper(newClient()) });

    await act(async () => {
      await result.current.mutateAsync({ id: "a2" });
    });

    expect(apiPost).toHaveBeenCalledWith("/approvals/a2/deny", {
      reason: "",
      decided_by: "human",
    });
  });
});
