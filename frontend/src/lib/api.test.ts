import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "./api";
import { showToast } from "@/components/Toast";

// api.ts calls showToast from inside the transport layer, so every error
// path has a user-visible side effect. Mocking it here both isolates the
// tests from React and lets us pin that contract.
vi.mock("@/components/Toast", () => ({ showToast: vi.fn() }));

const showToastMock = vi.mocked(showToast);

/** A minimal stand-in for the parts of Response that api.ts touches. */
function jsonResponse(body: unknown, status = 200, statusText = "OK") {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText,
    json: async () => body,
  } as unknown as Response;
}

/** A response whose body is not valid JSON — exercises the `.catch()` fallback. */
function unparseableResponse(status: number, statusText: string) {
  return {
    ok: false,
    status,
    statusText,
    json: async () => {
      throw new SyntaxError("Unexpected token < in JSON");
    },
  } as unknown as Response;
}

function abortError() {
  const err = new Error("The operation was aborted.");
  err.name = "AbortError";
  return err;
}

/** Never resolves on its own; rejects with an AbortError when its signal
 *  fires — and, like a real fetch, rejects *immediately* if the signal is
 *  already aborted before the call rather than waiting for an event that
 *  has already been dispatched. */
function hangingFetch() {
  return vi.fn(
    (_url: string, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        if (init?.signal?.aborted) {
          reject(abortError());
          return;
        }
        init?.signal?.addEventListener("abort", () => reject(abortError()));
      }),
  );
}

function mockFetch(impl: (url: string, init?: RequestInit) => Promise<Response>) {
  const fn = vi.fn(impl);
  vi.stubGlobal("fetch", fn);
  return fn;
}

/** Awaits a request that is expected to reject and hands back the ApiError.
 *  `api.get(...).catch(e => e)` cannot be used for this: the callback's
 *  return type unions with the request's own `unknown`, which collapses the
 *  whole result straight back to `unknown`. */
async function expectApiError(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (err) {
    if (err instanceof ApiError) return err;
    throw err;
  }
  throw new Error("expected the request to reject, but it resolved");
}

afterEach(() => {
  vi.unstubAllGlobals();
  showToastMock.mockClear();
});

describe("api URL construction", () => {
  it("prefixes every relative path with /api/v1", async () => {
    const fetchMock = mockFetch(async () => jsonResponse({ ok: true }));
    await api.get("/workflows");
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/workflows", expect.anything());
  });

  it("getRaw bypasses the prefix for endpoints served elsewhere", async () => {
    const fetchMock = mockFetch(async () => jsonResponse({ name: "massclaw" }));
    await api.getRaw("/.well-known/agent-facts.json");
    expect(fetchMock).toHaveBeenCalledWith("/.well-known/agent-facts.json", expect.anything());
    // The prefix must not leak in — this endpoint 404s under /api/v1.
    expect(fetchMock.mock.calls[0][0]).not.toContain("/api/v1");
  });

  it("sends JSON content-type and lets callers override headers", async () => {
    const fetchMock = mockFetch(async () => jsonResponse({}));
    await api.post("/workflows", { goal: "x" }, {
      headers: { "X-Trace": "abc" },
    } as Parameters<typeof api.post>[2]);
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.headers).toMatchObject({
      "Content-Type": "application/json",
      "X-Trace": "abc",
    });
  });
});

describe("api verbs and bodies", () => {
  it("POST serialises a body and omits it when absent", async () => {
    const fetchMock = mockFetch(async () => jsonResponse({}));
    await api.post("/approvals/a1/approve", { reason: "looks fine" });
    expect((fetchMock.mock.calls[0][1] as RequestInit).body).toBe(
      JSON.stringify({ reason: "looks fine" }),
    );

    await api.post("/approvals/a1/approve");
    expect((fetchMock.mock.calls[1][1] as RequestInit).body).toBeUndefined();
  });

  it("PATCH and DELETE set their methods", async () => {
    const fetchMock = mockFetch(async () => jsonResponse({}));
    await api.patch("/projects/p1", { name: "renamed" });
    await api.delete("/projects/p1");
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("PATCH");
    expect((fetchMock.mock.calls[1][1] as RequestInit).method).toBe("DELETE");
  });

  it("returns undefined for 204 instead of parsing an empty body", async () => {
    const json = vi.fn();
    mockFetch(async () => ({ ok: true, status: 204, statusText: "No Content", json }) as unknown as Response);
    await expect(api.delete("/projects/p1")).resolves.toBeUndefined();
    expect(json).not.toHaveBeenCalled();
  });

  it("parses and returns the JSON body on success", async () => {
    mockFetch(async () => jsonResponse({ workflow_id: "w1", total: 3 }));
    await expect(api.get<{ workflow_id: string; total: number }>("/workflows")).resolves.toEqual({
      workflow_id: "w1",
      total: 3,
    });
  });
});

describe("ApiError shape", () => {
  it("carries status, code, message and nextSteps from the error envelope", async () => {
    mockFetch(async () =>
      jsonResponse(
        {
          detail: "Budget exhausted for this workflow",
          error_code: "BUDGET_EXHAUSTED",
          next_steps: "Raise the budget limit and resume",
        },
        402,
      ),
    );

    const err = await expectApiError(api.get("/workflows/w1/resume"));
    expect(err).toMatchObject({
      status: 402,
      code: "BUDGET_EXHAUSTED",
      message: "Budget exhausted for this workflow",
      nextSteps: "Raise the budget limit and resume",
    });
  });

  it("unwraps a structured detail object via detail.message", async () => {
    mockFetch(async () =>
      jsonResponse({ detail: { message: "Approval already decided", code: "CONFLICT" } }, 409),
    );
    const err = await expectApiError(api.post("/approvals/a1/approve"));
    expect(err.message).toBe("Approval already decided");
    // No error_code at the top level, so the code falls back to UNKNOWN.
    expect(err.code).toBe("UNKNOWN");
  });

  it("falls back to statusText when the body is not JSON", async () => {
    mockFetch(async () => unparseableResponse(502, "Bad Gateway"));
    const err = await expectApiError(api.get("/system/metrics"));
    expect(err.message).toBe("Bad Gateway");
    expect(err.status).toBe(502);
  });

  it("leaves nextSteps undefined when the field is not a string", async () => {
    mockFetch(async () => jsonResponse({ detail: "nope", next_steps: { a: 1 } }, 400));
    const err = await expectApiError(api.get("/tools"));
    expect(err.nextSteps).toBeUndefined();
  });

  it("rethrows non-abort transport failures untouched", async () => {
    const boom = new TypeError("Failed to fetch");
    mockFetch(async () => {
      throw boom;
    });
    await expect(api.get("/workflows")).rejects.toBe(boom);
    // A network-level failure is not the transport layer's to narrate.
    expect(showToastMock).not.toHaveBeenCalled();
  });
});

describe("global toast side effect", () => {
  it("toasts detail and nextSteps together when both are present", async () => {
    mockFetch(async () =>
      jsonResponse({ detail: "Budget exhausted", next_steps: "Raise the limit" }, 402),
    );
    await api.get("/workflows").catch(() => {});
    expect(showToastMock).toHaveBeenCalledWith("Budget exhausted — Raise the limit");
  });

  it("toasts detail alone when nextSteps is absent", async () => {
    mockFetch(async () => jsonResponse({ detail: "Not found" }, 404));
    await api.get("/workflows/nope").catch(() => {});
    expect(showToastMock).toHaveBeenCalledWith("Not found");
  });

  it("stays silent on success", async () => {
    mockFetch(async () => jsonResponse({}));
    await api.get("/workflows");
    expect(showToastMock).not.toHaveBeenCalled();
  });
});

describe("timeout and cancellation", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("aborts after the default 30s and reports a TIMEOUT ApiError", async () => {
    mockFetch(hangingFetch());
    const pending = expectApiError(api.get("/workflows"));

    // One tick short of the deadline: still in flight.
    await vi.advanceTimersByTimeAsync(29_999);
    let settled = false;
    void pending.then(() => {
      settled = true;
    });
    await Promise.resolve();
    expect(settled).toBe(false);

    await vi.advanceTimersByTimeAsync(1);
    const err = await pending;
    expect(err.status).toBe(0);
    expect(err.code).toBe("TIMEOUT");
    expect(err.message).toContain("timed out after 30000ms");
    expect(err.message).toContain("/api/v1/workflows");
    expect(showToastMock).toHaveBeenCalledWith(expect.stringContaining("timed out"));
  });

  it("honours a caller-supplied timeoutMs", async () => {
    mockFetch(hangingFetch());
    const pending = expectApiError(api.get("/workflows", { timeoutMs: 500 }));
    await vi.advanceTimersByTimeAsync(500);
    const err = await pending;
    expect(err.code).toBe("TIMEOUT");
    expect(err.message).toContain("timed out after 500ms");
  });

  it("clears the timeout timer once a request succeeds", async () => {
    const clearSpy = vi.spyOn(globalThis, "clearTimeout");
    mockFetch(async () => jsonResponse({}));
    await api.get("/workflows");
    expect(clearSpy).toHaveBeenCalled();
  });

  it("aborts immediately when the caller's signal is already aborted", async () => {
    const fetchMock = mockFetch(hangingFetch());
    const controller = new AbortController();
    controller.abort();

    const err = await expectApiError(api.get("/workflows", { signal: controller.signal }));
    expect(err.code).toBe("TIMEOUT");
    // The internal signal handed to fetch must already be aborted.
    expect((fetchMock.mock.calls[0][1] as RequestInit).signal?.aborted).toBe(true);
  });

  it("propagates a later abort from the caller's signal", async () => {
    mockFetch(hangingFetch());
    const controller = new AbortController();
    const pending = expectApiError(api.get("/workflows", { signal: controller.signal }));

    await vi.advanceTimersByTimeAsync(10);
    controller.abort();
    const err = await pending;
    // Caller cancellation currently surfaces through the same TIMEOUT
    // branch as a real timeout — pinned here so a future split is a
    // deliberate change rather than an accident.
    expect(err.code).toBe("TIMEOUT");
  });
});
