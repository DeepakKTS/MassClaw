import { showToast } from "@/components/Toast";

const API_BASE = "/api/v1";

// Default timeout: 30s covers every non-SSE endpoint (SSE streams set
// their own lifecycle). Long enough for a slow first LLM call, short
// enough that a silently dead server fails the UI instead of hanging
// a spinner forever.
const DEFAULT_TIMEOUT_MS = 30_000;

class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public nextSteps?: string,
  ) {
    super(message);
  }
}

async function requestAbsolute<T>(
  url: string,
  options: RequestInit & { timeoutMs?: number } = {},
): Promise<T> {
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  // Merge caller-supplied signal with our timeout signal if both exist.
  const externalSignal = options.signal;
  if (externalSignal) {
    if (externalSignal.aborted) controller.abort();
    else externalSignal.addEventListener("abort", () => controller.abort(), { once: true });
  }

  try {
    const res = await fetch(url, {
      ...options,
      signal: controller.signal,
      headers: { "Content-Type": "application/json", ...options.headers },
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      const detail = typeof err.detail === "string" ? err.detail : err.detail?.message || res.statusText;
      const nextSteps = typeof err.next_steps === "string" ? err.next_steps : undefined;
      const apiError = new ApiError(res.status, err.error_code || "UNKNOWN", detail, nextSteps);
      showToast(nextSteps ? `${detail} — ${nextSteps}` : detail);
      throw apiError;
    }
    if (res.status === 204) return undefined as T;
    return res.json();
  } catch (err) {
    if ((err as Error).name === "AbortError") {
      const msg = `Request to ${url} timed out after ${timeoutMs}ms`;
      showToast(msg);
      throw new ApiError(0, "TIMEOUT", msg);
    }
    throw err;
  } finally {
    clearTimeout(timeoutId);
  }
}

function request<T>(path: string, options: RequestInit & { timeoutMs?: number } = {}): Promise<T> {
  return requestAbsolute<T>(`${API_BASE}${path}`, options);
}

export const api = {
  get: <T>(path: string, options?: { timeoutMs?: number; signal?: AbortSignal }) =>
    request<T>(path, options),
  post: <T>(path: string, body?: unknown, options?: { timeoutMs?: number; signal?: AbortSignal }) =>
    request<T>(path, {
      ...options,
      method: "POST",
      body: body ? JSON.stringify(body) : undefined,
    }),
  patch: <T>(path: string, body: unknown, options?: { timeoutMs?: number }) =>
    request<T>(path, { ...options, method: "PATCH", body: JSON.stringify(body) }),
  delete: <T>(path: string, options?: { timeoutMs?: number }) =>
    request<T>(path, { ...options, method: "DELETE" }),
  // getRaw bypasses the /api/v1 prefix for endpoints served at other paths
  // (e.g. `/.well-known/agent-facts.json`). Same error / toast contract.
  getRaw: <T>(url: string, options?: { timeoutMs?: number }) => requestAbsolute<T>(url, options),
};

export { ApiError };
