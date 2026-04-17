"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { AuditLogEntry } from "@/types/audit";
import type { PaginatedResponse } from "@/types/common";
import type { PolicyDecisionAudit } from "@/types/policy";
import { useState } from "react";

type TabKey = "events" | "policy";

function actionClass(action: string) {
  if (action === "deny") return "bg-red-500/10 text-red-400";
  if (action === "escalate_human") return "bg-amber-500/10 text-amber-400";
  if (action === "allow") return "bg-emerald-500/10 text-emerald-400";
  return "bg-white/5 text-massclaw-text-muted";
}

function EventsTab() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["audit-search"],
    queryFn: () => api.get<PaginatedResponse<AuditLogEntry>>("/audit/search?page_size=50"),
  });

  return (
    <div className="glass rounded-xl overflow-hidden font-mono">
      <div className="p-4 border-b border-white/[0.06] flex items-center gap-2">
        <div className="w-2 h-2 rounded-full bg-emerald-400" />
        <span className="text-[10px] text-emerald-400 uppercase tracking-wider">System Log</span>
      </div>
      <div className="divide-y divide-white/[0.04]">
        {error && (
          <div className="p-4 text-red-400 text-xs">Failed to load: {error.message}</div>
        )}
        {isLoading ? (
          <div className="p-4 text-massclaw-text-muted text-sm">Loading…</div>
        ) : (
          data?.items?.map((log: AuditLogEntry) => (
            <div
              key={log.log_id}
              className="px-4 py-3 hover:bg-white/[0.02] transition-colors text-xs"
            >
              <div className="flex items-center gap-3">
                <span className="text-massclaw-text-muted whitespace-nowrap">
                  {new Date(log.created_at).toLocaleString()}
                </span>
                <span className="px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400 text-[10px]">
                  {log.event_type}
                </span>
                <span className="text-massclaw-text-muted">{log.actor_id}</span>
                <span className="text-massclaw-text truncate flex-1">
                  {log.input_summary || log.output_summary || "\u2014"}
                </span>
              </div>
            </div>
          ))
        )}
        {!isLoading && !data?.items?.length && (
          <div className="p-8 text-center text-massclaw-text-muted text-sm">No audit events yet</div>
        )}
      </div>
    </div>
  );
}

function PolicyDecisionsTab() {
  const [actionFilter, setActionFilter] = useState<string>("");
  const [expanded, setExpanded] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["policy-decisions", actionFilter],
    queryFn: () =>
      api.get<PolicyDecisionAudit[]>(
        `/audit/policy-decisions?limit=100${actionFilter ? `&action=${actionFilter}` : ""}`,
      ),
  });

  const decisions = data ?? [];

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs text-massclaw-text-muted">filter:</span>
        {(["", "deny", "escalate_human", "allow"] as const).map((a) => (
          <button
            key={a || "all"}
            onClick={() => setActionFilter(a)}
            className={`text-[11px] px-3 py-1 rounded-full border transition-colors ${
              actionFilter === a
                ? "border-accent-500/40 text-accent-300 bg-accent-500/10"
                : "border-white/10 text-massclaw-text-muted hover:bg-white/5"
            }`}
          >
            {a || "all"}
          </button>
        ))}
      </div>

      {error && (
        <div className="glass rounded-xl p-4 border border-red-500/20 text-red-400 text-sm">
          Failed to load: {error.message}
        </div>
      )}

      <div className="glass rounded-xl overflow-hidden">
        <div className="divide-y divide-white/[0.04]">
          {isLoading ? (
            <div className="p-4 text-massclaw-text-muted text-sm">Loading…</div>
          ) : decisions.length === 0 ? (
            <div className="p-8 text-center text-massclaw-text-muted text-sm">
              No policy decisions recorded yet. Decisions appear here as the scheduler runs.
            </div>
          ) : (
            decisions.map((d) => {
              const isOpen = expanded === d.content_hash;
              return (
                <div key={d.content_hash} className="text-xs">
                  <button
                    onClick={() => setExpanded(isOpen ? null : d.content_hash)}
                    className="w-full text-left px-4 py-3 hover:bg-white/[0.02] transition-colors flex items-center gap-3"
                  >
                    <span className="text-massclaw-text-muted whitespace-nowrap font-mono">
                      {d.created_at ? new Date(d.created_at).toLocaleString() : "—"}
                    </span>
                    <span
                      className={`text-[10px] px-1.5 py-0.5 rounded uppercase tracking-wider ${actionClass(d.decision.action)}`}
                    >
                      {d.decision.action}
                    </span>
                    <span className="font-mono text-massclaw-text truncate flex-1">
                      {d.decision.rule_id || "—"}: {d.decision.reason || "—"}
                    </span>
                    <span className="text-[10px] text-massclaw-text-muted whitespace-nowrap font-mono">
                      {d.content_hash.slice(0, 12)}…
                    </span>
                  </button>
                  {isOpen && (
                    <div className="px-4 py-3 bg-black/30 border-t border-white/[0.04] space-y-3">
                      <div>
                        <div className="text-[10px] text-massclaw-text-muted uppercase tracking-wider mb-1">
                          Decision
                        </div>
                        <pre className="text-[10px] text-massclaw-text bg-black/40 p-2 rounded overflow-x-auto">
                          {JSON.stringify(d.decision, null, 2)}
                        </pre>
                      </div>
                      <div>
                        <div className="text-[10px] text-massclaw-text-muted uppercase tracking-wider mb-1">
                          Evaluated Context
                        </div>
                        <pre className="text-[10px] text-massclaw-text bg-black/40 p-2 rounded overflow-x-auto">
                          {JSON.stringify(d.context, null, 2)}
                        </pre>
                      </div>
                      <div className="text-[10px] text-massclaw-text-muted font-mono">
                        signed by <span className="text-massclaw-text">{d.author_did}</span>
                      </div>
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}

export default function AuditPage() {
  const [tab, setTab] = useState<TabKey>("events");
  return (
    <div className="max-w-5xl mx-auto space-y-6 py-6">
      <div>
        <h1 className="text-heading">Audit Trail</h1>
        <p className="text-massclaw-text-muted mt-1 text-sm">
          Complete, signed decision log across the system.
        </p>
      </div>

      <div className="flex items-center gap-2 border-b border-white/[0.06]">
        {(
          [
            { id: "events", label: "Events" },
            { id: "policy", label: "Policy Decisions" },
          ] as { id: TabKey; label: string }[]
        ).map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-4 py-2 text-xs border-b-2 transition-colors ${
              tab === t.id
                ? "border-accent-500 text-accent-300"
                : "border-transparent text-massclaw-text-muted hover:text-massclaw-text"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "events" ? <EventsTab /> : <PolicyDecisionsTab />}
    </div>
  );
}
