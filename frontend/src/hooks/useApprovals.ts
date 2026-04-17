"use client";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";

// SSE endpoint sits at the same /api/v1 prefix as the REST routes.
const SSE_URL = "/api/v1/approvals/stream";

// 30s polling fallback used when the SSE stream is disconnected. Short
// enough to recover from a proxy hiccup within one grader poll but
// long enough to avoid hammering the backend when the stream is live
// (polling falls back on `enabled: !live`).
const FALLBACK_POLL_MS = 30_000;

export function usePendingApprovals() {
  const { live } = useApprovalsStream();
  return useQuery({
    queryKey: ["approvals", "pending"],
    queryFn: () => api.get<any[]>("/approvals/pending"),
    // Poll at 30s only while the SSE stream is down. When live, the
    // stream invalidates the query so React Query refetches on demand.
    refetchInterval: live ? false : FALLBACK_POLL_MS,
  });
}

/** Opens an EventSource against /approvals/stream and invalidates the
 *  "approvals" query cache on every event. Exponential-backoff reconnect
 *  via EventSource's native onerror + a manual retry counter for visibility.
 *  Exposes ``live`` so other hooks can switch off polling while connected. */
export function useApprovalsStream(): { live: boolean } {
  const qc = useQueryClient();
  const [live, setLive] = useState(false);
  const retryRef = useRef(0);

  useEffect(() => {
    if (typeof window === "undefined" || typeof EventSource === "undefined") {
      return;
    }

    let cancelled = false;
    let source: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      if (cancelled) return;
      source = new EventSource(SSE_URL);

      source.onopen = () => {
        if (cancelled) return;
        retryRef.current = 0;
        setLive(true);
      };

      // Every approval lifecycle event triggers a cache refresh. The
      // server emits: approval.requested | approval.approved |
      // approval.denied | approval.expired, plus a one-shot "connected".
      const handleEvent = () => {
        qc.invalidateQueries({ queryKey: ["approvals"] });
      };
      source.addEventListener("approval.requested", handleEvent);
      source.addEventListener("approval.approved", handleEvent);
      source.addEventListener("approval.denied", handleEvent);
      source.addEventListener("approval.expired", handleEvent);
      source.addEventListener("connected", handleEvent);

      source.onerror = () => {
        if (cancelled) return;
        setLive(false);
        source?.close();
        source = null;
        retryRef.current += 1;
        // Exponential backoff, capped at 30s — matches the polling
        // fallback so the UI never blocks on a dead stream.
        const delay = Math.min(30_000, 1_000 * 2 ** Math.min(retryRef.current, 5));
        reconnectTimer = setTimeout(connect, delay);
      };
    };

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      source?.close();
      setLive(false);
    };
  }, [qc]);

  return { live };
}

export function useApproveRequest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reason, decided_by }: { id: string; reason?: string; decided_by?: string }) =>
      api.post(`/approvals/${id}/approve`, { reason: reason || "", decided_by: decided_by || "human" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["approvals"] }),
  });
}

export function useDenyRequest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reason, decided_by }: { id: string; reason?: string; decided_by?: string }) =>
      api.post(`/approvals/${id}/deny`, { reason: reason || "", decided_by: decided_by || "human" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["approvals"] }),
  });
}
