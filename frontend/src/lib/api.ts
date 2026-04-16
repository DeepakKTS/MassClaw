import { showToast } from "@/components/Toast";

const API_BASE = "/api/v1";

class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

async function requestAbsolute<T>(url: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    const message = err.detail || res.statusText;
    const apiError = new ApiError(res.status, err.error_code || "UNKNOWN", message);
    showToast(message);
    throw apiError;
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  return requestAbsolute<T>(`${API_BASE}${path}`, options);
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) => request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  patch: <T>(path: string, body: unknown) => request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  // getRaw bypasses the /api/v1 prefix for endpoints served at other paths
  // (e.g. `/.well-known/agent-facts.json`). Same error / toast contract as get().
  getRaw: <T>(url: string) => requestAbsolute<T>(url),
};

export { ApiError };
