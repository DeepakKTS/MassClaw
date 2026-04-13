"use client";

import { useParams } from "next/navigation";
import { useWorkflow, useWorkflowStatus, useWorkflowTasks } from "@/hooks/useWorkflows";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { getStatusColor, formatCredits, formatDuration, cn } from "@/lib/utils";
import {
  CheckCircle, XCircle, Clock, Loader2, SkipForward,
  GitBranch, DollarSign, Timer, AlertTriangle, ChevronDown, ChevronRight,
  Brain, Shield,
} from "lucide-react";
import { useState } from "react";

const STATUS_ICON: Record<string, any> = {
  completed: CheckCircle,
  failed: XCircle,
  running: Loader2,
  pending: Clock,
  skipped: SkipForward,
  assigned: Clock,
  retrying: Loader2,
};

const STATUS_BG: Record<string, string> = {
  completed: "border-emerald-500/30 bg-emerald-500/5",
  failed: "border-red-500/30 bg-red-500/5",
  running: "border-indigo-500/30 bg-indigo-500/5",
  pending: "border-massclaw-border bg-massclaw-surface",
  skipped: "border-massclaw-border bg-massclaw-surface opacity-50",
};

export default function WorkflowDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data: workflow, isLoading } = useWorkflow(id);
  const { data: status } = useWorkflowStatus(id);
  const { data: tasks } = useWorkflowTasks(id);
  const { data: walletBalance } = useQuery({
    queryKey: ["wallet-balance", id],
    queryFn: () => api.get<any>(`/wallet/workflow/${id}/balance`),
    enabled: !!id,
  });
  const [expandedTask, setExpandedTask] = useState<string | null>(null);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="animate-spin text-massclaw-accent" size={32} />
        <span className="ml-3 text-massclaw-text-muted">Loading workflow...</span>
      </div>
    );
  }

  if (!workflow) return <div className="text-massclaw-text-muted">Workflow not found</div>;

  const result = workflow.result as Record<string, any> | null;
  const isTerminal = ["completed", "failed", "cancelled"].includes(workflow.status);

  return (
    <div className="space-y-6 max-w-5xl">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div className="flex-1">
          <div className="flex items-center gap-3">
            <GitBranch size={24} className="text-massclaw-accent" />
            <h1 className="text-2xl font-bold">Workflow</h1>
            <span className={cn(
              "text-xs font-semibold px-2.5 py-1 rounded-full uppercase tracking-wide",
              workflow.status === "completed" && "bg-emerald-500/15 text-emerald-400",
              workflow.status === "failed" && "bg-red-500/15 text-red-400",
              workflow.status === "running" && "bg-indigo-500/15 text-indigo-400 animate-pulse",
              workflow.status === "pending" && "bg-gray-500/15 text-gray-400",
              workflow.status === "cancelled" && "bg-gray-500/15 text-gray-400",
            )}>
              {workflow.status}
            </span>
            {workflow.domain && (
              <span className="text-xs px-2 py-0.5 rounded bg-massclaw-border text-massclaw-text-muted">
                {workflow.domain}
              </span>
            )}
          </div>
          <p className="text-massclaw-text-muted mt-2 leading-relaxed max-w-3xl">{workflow.prompt}</p>
        </div>
      </div>

      {/* Progress + Stats */}
      {status && (
        <div className="bg-massclaw-surface border border-massclaw-border rounded-xl p-5">
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm font-medium">Progress</span>
            <span className="text-sm font-bold text-massclaw-accent">{status.progress_percent}%</span>
          </div>
          <div className="w-full bg-massclaw-border/50 rounded-full h-2.5 overflow-hidden">
            <div
              className={cn(
                "h-full rounded-full transition-all duration-700 ease-out",
                status.failed_tasks > 0 ? "bg-gradient-to-r from-massclaw-accent to-massclaw-danger" : "bg-gradient-to-r from-massclaw-accent to-emerald-500"
              )}
              style={{ width: `${status.progress_percent}%` }}
            />
          </div>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mt-5">
            <StatBox icon={CheckCircle} label="Completed" value={`${status.completed_tasks}/${status.total_tasks}`} color="text-emerald-400" />
            <StatBox icon={AlertTriangle} label="Failed" value={status.failed_tasks} color={status.failed_tasks > 0 ? "text-red-400" : "text-massclaw-text-muted"} />
            <StatBox icon={DollarSign} label="Cost" value={formatCredits(status.budget_used)} color="text-amber-400" />
            <StatBox icon={Shield} label="Budget" value={`${((status.budget_used / status.budget_limit) * 100).toFixed(0)}% used`} color="text-massclaw-text-muted" />
            <StatBox icon={Timer} label="Duration" value={status.elapsed_seconds ? `${status.elapsed_seconds.toFixed(1)}s` : "—"} color="text-blue-400" />
          </div>
        </div>
      )}

      {/* Task Pipeline */}
      <div>
        <h2 className="text-lg font-semibold mb-3 flex items-center gap-2">
          <span>Task Pipeline</span>
          {tasks && <span className="text-xs text-massclaw-text-muted font-normal">({tasks.length} steps)</span>}
        </h2>
        <div className="space-y-2">
          {tasks?.map((task, idx) => {
            const Icon = STATUS_ICON[task.status] || Clock;
            const isExpanded = expandedTask === task.task_id;
            const output = task.output as Record<string, any> | null;

            return (
              <div
                key={task.task_id}
                className={cn(
                  "border rounded-xl transition-all",
                  STATUS_BG[task.status] || "border-massclaw-border bg-massclaw-surface",
                )}
              >
                <div
                  className="flex items-center gap-4 p-4 cursor-pointer hover:bg-white/[0.02] transition"
                  onClick={() => setExpandedTask(isExpanded ? null : task.task_id)}
                >
                  {/* Step number */}
                  <div className="w-8 h-8 rounded-lg bg-massclaw-border/50 flex items-center justify-center text-sm font-mono font-bold text-massclaw-text-muted">
                    {task.step_number}
                  </div>

                  {/* Status icon */}
                  <Icon
                    size={18}
                    className={cn(
                      getStatusColor(task.status),
                      task.status === "running" && "animate-spin",
                    )}
                  />

                  {/* Capability + description */}
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-semibold capitalize">{task.capability.replace(/-/g, " ")}</p>
                    {task.error_message && (
                      <p className="text-xs text-massclaw-danger mt-0.5 truncate">{task.error_message}</p>
                    )}
                  </div>

                  {/* Metrics */}
                  <div className="flex items-center gap-4 text-xs text-massclaw-text-muted">
                    {task.latency_ms != null && (
                      <span className="flex items-center gap-1">
                        <Timer size={12} />
                        {formatDuration(task.latency_ms)}
                      </span>
                    )}
                    <span className="flex items-center gap-1">
                      <DollarSign size={12} />
                      {formatCredits(task.cost_used)}
                    </span>
                    {task.confidence != null && (
                      <span className="flex items-center gap-1">
                        <Shield size={12} />
                        {(task.confidence * 100).toFixed(0)}%
                      </span>
                    )}
                  </div>

                  {/* Expand arrow */}
                  {output?.content && (
                    isExpanded
                      ? <ChevronDown size={16} className="text-massclaw-text-muted" />
                      : <ChevronRight size={16} className="text-massclaw-text-muted" />
                  )}
                </div>

                {/* Expanded output */}
                {isExpanded && output?.content && (
                  <div className="px-4 pb-4 pt-0">
                    <div className="border-t border-massclaw-border/50 pt-3 mt-1">
                      <p className="text-xs font-medium text-massclaw-text-muted mb-2 uppercase tracking-wide">Agent Output</p>
                      <div className="bg-massclaw-bg rounded-lg p-4 text-sm leading-relaxed whitespace-pre-wrap max-h-96 overflow-auto">
                        {output.content}
                      </div>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
          {!tasks?.length && (
            <div className="text-center py-8 text-massclaw-text-muted">
              <Clock size={24} className="mx-auto mb-2 opacity-50" />
              <p>No tasks yet — workflow is being decomposed</p>
            </div>
          )}
        </div>
      </div>

      {/* Wallet Summary */}
      {walletBalance && (
        <div className="bg-massclaw-surface border border-massclaw-border rounded-xl p-5">
          <h2 className="text-sm font-semibold mb-3 flex items-center gap-2">
            <DollarSign size={16} className="text-amber-400" />
            Wallet
          </h2>
          <div className="grid grid-cols-4 gap-4 text-sm">
            <div>
              <p className="text-massclaw-text-muted text-xs">Budget</p>
              <p className="font-medium">{formatCredits(walletBalance.budget_limit)}</p>
            </div>
            <div>
              <p className="text-massclaw-text-muted text-xs">Used</p>
              <p className="font-medium">{formatCredits(walletBalance.budget_used)}</p>
            </div>
            <div>
              <p className="text-massclaw-text-muted text-xs">Remaining</p>
              <p className="font-medium text-emerald-400">{formatCredits(walletBalance.budget_remaining)}</p>
            </div>
            <div>
              <p className="text-massclaw-text-muted text-xs">Utilization</p>
              <p className="font-medium">{((walletBalance.budget_used / walletBalance.budget_limit) * 100).toFixed(1)}%</p>
            </div>
          </div>
        </div>
      )}

      {/* Final Result */}
      {isTerminal && result && (
        <div>
          <h2 className="text-lg font-semibold mb-3 flex items-center gap-2">
            <Brain size={20} className="text-massclaw-accent" />
            Synthesized Result
          </h2>
          <div className="bg-massclaw-surface border border-massclaw-accent/20 rounded-xl p-6">
            {/* Confidence badge */}
            <div className="flex items-center gap-3 mb-4">
              {result.confidence != null && (
                <span className={cn(
                  "text-xs font-semibold px-2.5 py-1 rounded-full",
                  result.confidence >= 0.8 ? "bg-emerald-500/15 text-emerald-400" :
                  result.confidence >= 0.5 ? "bg-amber-500/15 text-amber-400" :
                  "bg-red-500/15 text-red-400"
                )}>
                  {(result.confidence * 100).toFixed(0)}% confidence
                </span>
              )}
              {result.summary && (
                <span className="text-xs text-massclaw-text-muted">{result.summary}</span>
              )}
            </div>

            {/* Result content - render as formatted text */}
            <div className="text-sm leading-7 text-massclaw-text whitespace-pre-wrap">
              {renderContent(result.content)}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* Small stat box component */
function StatBox({ icon: Icon, label, value, color }: { icon: any; label: string; value: any; color: string }) {
  return (
    <div className="flex items-center gap-2.5">
      <Icon size={16} className={color} />
      <div>
        <p className="text-[11px] text-massclaw-text-muted uppercase tracking-wide">{label}</p>
        <p className="text-sm font-semibold">{value}</p>
      </div>
    </div>
  );
}

/* Render content with basic markdown-like formatting */
function renderContent(content: string): React.ReactNode {
  if (!content) return <p className="text-massclaw-text-muted italic">No content</p>;

  // If content looks like JSON, try to render it nicely
  if (content.trim().startsWith("{") || content.trim().startsWith("[")) {
    try {
      const parsed = JSON.parse(content);
      // If it's a decomposition result, render tasks
      if (parsed.tasks && Array.isArray(parsed.tasks)) {
        return (
          <div className="space-y-3">
            <p className="font-medium">Domain: <span className="text-massclaw-accent">{parsed.domain}</span></p>
            <p className="text-massclaw-text-muted text-xs">Workflow decomposition ({parsed.tasks.length} tasks):</p>
            {parsed.tasks.map((t: any) => (
              <div key={t.id} className="bg-massclaw-bg rounded-lg p-3 border border-massclaw-border/50">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-mono font-bold text-massclaw-accent">{t.id}</span>
                  <span className="text-sm font-medium capitalize">{t.capability.replace(/-/g, " ")}</span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-massclaw-border text-massclaw-text-muted">{t.estimated_complexity}</span>
                </div>
                <p className="text-xs text-massclaw-text-muted mt-1">{t.description}</p>
                {t.depends_on.length > 0 && (
                  <p className="text-[10px] text-massclaw-text-muted mt-1">depends on: {t.depends_on.join(", ")}</p>
                )}
              </div>
            ))}
          </div>
        );
      }
      return <pre className="text-xs bg-massclaw-bg rounded-lg p-4 overflow-auto">{JSON.stringify(parsed, null, 2)}</pre>;
    } catch {
      // Not valid JSON, render as text
    }
  }

  // Render markdown-like text with basic formatting
  return content.split("\n").map((line, i) => {
    if (line.startsWith("### ")) return <h3 key={i} className="text-base font-bold mt-4 mb-1 text-massclaw-text">{line.slice(4)}</h3>;
    if (line.startsWith("## ")) return <h2 key={i} className="text-lg font-bold mt-5 mb-2 text-massclaw-text">{line.slice(3)}</h2>;
    if (line.startsWith("# ")) return <h1 key={i} className="text-xl font-bold mt-5 mb-2 text-massclaw-text">{line.slice(2)}</h1>;
    if (line.startsWith("- ") || line.startsWith("* ")) return <li key={i} className="ml-4 list-disc">{line.slice(2)}</li>;
    if (line.startsWith("**") && line.endsWith("**")) return <p key={i} className="font-bold mt-2">{line.slice(2, -2)}</p>;
    if (line.match(/^\d+\.\s/)) return <li key={i} className="ml-4 list-decimal">{line.replace(/^\d+\.\s/, "")}</li>;
    if (line.startsWith("|")) return <p key={i} className="font-mono text-xs bg-massclaw-bg px-2 py-0.5 rounded">{line}</p>;
    if (line.startsWith("---")) return <hr key={i} className="border-massclaw-border my-3" />;
    if (line.trim() === "") return <div key={i} className="h-2" />;
    return <p key={i}>{line}</p>;
  });
}
