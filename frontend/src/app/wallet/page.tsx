"use client";

import { useState, useMemo } from "react";
import { useWorkflows } from "@/hooks/useWorkflows";
import { useWalletBalance, useWalletEvents } from "@/hooks/useWallet";
import { formatNumber, cn, getStatusColor } from "@/lib/utils";
import { Wallet, ArrowUpRight, ArrowDownRight, RefreshCw, ChevronDown, ChevronUp } from "lucide-react";
import type { Workflow } from "@/types/workflow";
import type { WalletBalance, WalletEvent, WalletActionType } from "@/types/wallet";

/* ---------- Action type styling helpers ---------- */

const ACTION_STYLE: Record<WalletActionType, { label: string; color: string }> = {
  credit: { label: "Credit", color: "text-massclaw-success bg-massclaw-success/10" },
  debit: { label: "Debit", color: "text-massclaw-danger bg-massclaw-danger/10" },
  reserve: { label: "Reserve", color: "text-massclaw-warning bg-massclaw-warning/10" },
  release: { label: "Release", color: "text-massclaw-accent bg-massclaw-accent/10" },
};

/* ---------- Budget bar ---------- */

function BudgetBar({ used, limit }: { used: number; limit: number }) {
  const pct = limit > 0 ? Math.min((used / limit) * 100, 100) : 0;
  const barColor =
    pct >= 90 ? "bg-massclaw-danger" : pct >= 70 ? "bg-massclaw-warning" : "bg-massclaw-accent";

  return (
    <div className="w-full h-1.5 rounded-full bg-white/[0.06] overflow-hidden">
      <div className={cn("h-full rounded-full transition-all", barColor)} style={{ width: `${pct}%` }} />
    </div>
  );
}

/* ---------- Workflow row with expandable events ---------- */

function WorkflowWalletRow({ workflow }: { workflow: Workflow }) {
  const [expanded, setExpanded] = useState(false);
  const [eventsPage, setEventsPage] = useState(1);

  const { data: balance } = useWalletBalance(workflow.workflow_id);
  const { data: eventsData, isLoading: eventsLoading } = useWalletEvents(
    expanded ? workflow.workflow_id : "",
    eventsPage
  );

  const remaining = balance ? balance.budget_remaining : workflow.budget_limit - workflow.budget_used;
  const pct = workflow.budget_limit > 0 ? (workflow.budget_used / workflow.budget_limit) * 100 : 0;

  return (
    <div className="glass rounded-xl overflow-hidden">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full text-left px-5 py-4 hover:bg-white/[0.03] transition-colors"
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3 min-w-0">
            <Wallet size={16} className="text-massclaw-accent shrink-0" />
            <div className="min-w-0">
              <p className="text-sm font-medium truncate">{workflow.prompt}</p>
              <p className="text-[10px] text-massclaw-text-muted mt-0.5 font-mono">
                {workflow.workflow_id.slice(0, 8)}
                <span className="mx-1.5">&middot;</span>
                <span className={getStatusColor(workflow.status)}>{workflow.status}</span>
              </p>
            </div>
          </div>
          <div className="flex items-center gap-6 shrink-0 ml-4">
            <div className="text-right">
              <p className="text-xs text-massclaw-text-muted">Used / Limit</p>
              <p className="text-sm font-mono">
                <span className="text-massclaw-text">{formatNumber(workflow.budget_used, 2)}</span>
                <span className="text-massclaw-text-muted"> / {formatNumber(workflow.budget_limit, 2)}</span>
              </p>
            </div>
            <div className="text-right hidden sm:block">
              <p className="text-xs text-massclaw-text-muted">Remaining</p>
              <p className={cn("text-sm font-mono", pct >= 90 ? "text-massclaw-danger" : "text-massclaw-success")}>
                {formatNumber(remaining, 2)}
              </p>
            </div>
            {balance && (
              <div className="text-right hidden md:block">
                <p className="text-xs text-massclaw-text-muted">Reserved</p>
                <p className="text-sm font-mono text-massclaw-warning">{formatNumber(balance.reserved, 2)}</p>
              </div>
            )}
            {expanded ? (
              <ChevronUp size={16} className="text-massclaw-text-muted" />
            ) : (
              <ChevronDown size={16} className="text-massclaw-text-muted" />
            )}
          </div>
        </div>
        <div className="mt-3">
          <BudgetBar used={workflow.budget_used} limit={workflow.budget_limit} />
        </div>
      </button>

      {expanded && (
        <div className="border-t border-white/[0.06]">
          <div className="px-5 py-3 flex items-center gap-2">
            <div className="w-1.5 h-1.5 rounded-full bg-massclaw-accent" />
            <span className="text-[10px] text-massclaw-accent uppercase tracking-wider font-medium">
              Transaction History
            </span>
          </div>
          {eventsLoading ? (
            <div className="px-5 pb-4 text-sm text-massclaw-text-muted">Loading transactions...</div>
          ) : eventsData?.items && eventsData.items.length > 0 ? (
            <>
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-white/[0.04]">
                    <th className="text-left px-5 py-2 text-massclaw-text-muted font-medium uppercase tracking-wider">Time</th>
                    <th className="text-left px-5 py-2 text-massclaw-text-muted font-medium uppercase tracking-wider">Type</th>
                    <th className="text-right px-5 py-2 text-massclaw-text-muted font-medium uppercase tracking-wider">Amount</th>
                    <th className="text-right px-5 py-2 text-massclaw-text-muted font-medium uppercase tracking-wider">Balance</th>
                    <th className="text-left px-5 py-2 text-massclaw-text-muted font-medium uppercase tracking-wider hidden md:table-cell">Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {eventsData.items.map((event: WalletEvent) => {
                    const style = ACTION_STYLE[event.action_type] || { label: event.action_type, color: "text-massclaw-text-muted" };
                    const isPositive = event.credit_delta >= 0;
                    return (
                      <tr key={event.wallet_event_id} className="border-b border-white/[0.02] hover:bg-white/[0.02] transition-colors">
                        <td className="px-5 py-2.5 text-massclaw-text-muted whitespace-nowrap">
                          {new Date(event.created_at).toLocaleString()}
                        </td>
                        <td className="px-5 py-2.5">
                          <span className={cn("px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase", style.color)}>
                            {style.label}
                          </span>
                        </td>
                        <td className="px-5 py-2.5 text-right font-mono">
                          <span className={cn("inline-flex items-center gap-0.5", isPositive ? "text-massclaw-success" : "text-massclaw-danger")}>
                            {isPositive ? <ArrowUpRight size={10} /> : <ArrowDownRight size={10} />}
                            {isPositive ? "+" : ""}{formatNumber(event.credit_delta, 4)}
                          </span>
                        </td>
                        <td className="px-5 py-2.5 text-right font-mono text-massclaw-text-muted">
                          {formatNumber(event.balance_after, 4)}
                        </td>
                        <td className="px-5 py-2.5 text-massclaw-text-muted truncate max-w-[200px] hidden md:table-cell">
                          {event.reason || "\u2014"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {eventsData.total > 20 && (
                <div className="flex justify-center gap-2 py-3">
                  <button
                    onClick={() => setEventsPage((p) => Math.max(1, p - 1))}
                    disabled={eventsPage === 1}
                    className="px-3 py-1 text-xs glass rounded-lg disabled:opacity-30"
                  >
                    Previous
                  </button>
                  <span className="px-3 py-1 text-xs text-massclaw-text-muted">
                    Page {eventsPage}
                  </span>
                  <button
                    onClick={() => setEventsPage((p) => p + 1)}
                    disabled={eventsData.items.length < 20}
                    className="px-3 py-1 text-xs glass rounded-lg disabled:opacity-30"
                  >
                    Next
                  </button>
                </div>
              )}
            </>
          ) : (
            <div className="px-5 pb-4 text-sm text-massclaw-text-muted">No transactions recorded</div>
          )}
        </div>
      )}
    </div>
  );
}

/* ---------- Main Wallet Page ---------- */

export default function WalletPage() {
  const [page, setPage] = useState(1);
  const { data, isLoading, error } = useWorkflows(page);

  const summary = useMemo(() => {
    if (!data?.items) return { totalBudget: 0, totalUsed: 0, totalRemaining: 0, count: 0 };
    let totalBudget = 0;
    let totalUsed = 0;
    for (const w of data.items) {
      totalBudget += w.budget_limit;
      totalUsed += w.budget_used;
    }
    return {
      totalBudget,
      totalUsed,
      totalRemaining: totalBudget - totalUsed,
      count: data.total,
    };
  }, [data]);

  const utilizationPct = summary.totalBudget > 0 ? (summary.totalUsed / summary.totalBudget) * 100 : 0;

  return (
    <div className="max-w-5xl mx-auto space-y-6 py-6">
      {/* Header */}
      <div>
        <h1 className="text-heading">Wallet</h1>
        <p className="text-massclaw-text-muted mt-1 text-sm">Budget tracking across all workflows</p>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="glass rounded-xl p-4">
          <p className="text-[10px] text-massclaw-text-muted uppercase tracking-wider">Total Budget</p>
          <p className="text-lg font-mono font-semibold mt-1">{formatNumber(summary.totalBudget, 2)}</p>
        </div>
        <div className="glass rounded-xl p-4">
          <p className="text-[10px] text-massclaw-text-muted uppercase tracking-wider">Total Spent</p>
          <p className="text-lg font-mono font-semibold mt-1 text-massclaw-danger">{formatNumber(summary.totalUsed, 2)}</p>
        </div>
        <div className="glass rounded-xl p-4">
          <p className="text-[10px] text-massclaw-text-muted uppercase tracking-wider">Remaining</p>
          <p className="text-lg font-mono font-semibold mt-1 text-massclaw-success">{formatNumber(summary.totalRemaining, 2)}</p>
        </div>
        <div className="glass rounded-xl p-4">
          <p className="text-[10px] text-massclaw-text-muted uppercase tracking-wider">Utilization</p>
          <p className={cn(
            "text-lg font-mono font-semibold mt-1",
            utilizationPct >= 90 ? "text-massclaw-danger" : utilizationPct >= 70 ? "text-massclaw-warning" : "text-massclaw-accent"
          )}>
            {formatNumber(utilizationPct, 1)}%
          </p>
        </div>
      </div>

      {/* Overall utilization bar */}
      <div className="glass rounded-xl p-4 space-y-2">
        <div className="flex items-center justify-between text-xs">
          <span className="text-massclaw-text-muted">Overall Budget Utilization</span>
          <span className="font-mono">{formatNumber(utilizationPct, 1)}%</span>
        </div>
        <BudgetBar used={summary.totalUsed} limit={summary.totalBudget} />
      </div>

      {/* Error State */}
      {error && (
        <div className="glass rounded-xl p-6 text-center border border-massclaw-danger/20">
          <p className="text-massclaw-danger text-sm">Failed to load workflows: {error.message}</p>
          <button
            onClick={() => window.location.reload()}
            className="mt-3 inline-flex items-center gap-2 px-4 py-2 text-sm rounded-lg bg-massclaw-danger/10 text-massclaw-danger hover:bg-massclaw-danger/20 transition-colors"
          >
            <RefreshCw size={14} />
            Retry
          </button>
        </div>
      )}

      {/* Workflow Wallets */}
      {isLoading ? (
        <div className="text-massclaw-text-muted text-sm">Loading workflows...</div>
      ) : data?.items && data.items.length > 0 ? (
        <div className="space-y-3">
          {data.items.map((workflow: Workflow) => (
            <WorkflowWalletRow key={workflow.workflow_id} workflow={workflow} />
          ))}
        </div>
      ) : !error ? (
        <div className="glass rounded-xl p-12 text-center">
          <Wallet size={32} className="mx-auto text-massclaw-text-muted/40" />
          <p className="text-massclaw-text-muted text-sm mt-3">No workflows with budget data</p>
        </div>
      ) : null}

      {/* Pagination */}
      {data && data.total > 20 && (
        <div className="flex justify-center gap-2">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
            className="px-3 py-1 text-sm glass rounded-lg disabled:opacity-30"
          >
            Previous
          </button>
          <span className="px-3 py-1 text-sm text-massclaw-text-muted">Page {page}</span>
          <button
            onClick={() => setPage((p) => p + 1)}
            disabled={(data?.items?.length ?? 0) < 20}
            className="px-3 py-1 text-sm glass rounded-lg disabled:opacity-30"
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
