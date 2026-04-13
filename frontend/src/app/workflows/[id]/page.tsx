"use client";

import { useParams } from "next/navigation";
import { useWorkflow, useWorkflowStatus, useWorkflowTasks } from "@/hooks/useWorkflows";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { formatCredits, cn } from "@/lib/utils";
import {
  GitBranch, DollarSign, CheckCheck, Loader2,
} from "lucide-react";
import AgentPlan from "@/components/ui/agent-plan";

export default function WorkflowDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data: workflow, isLoading } = useWorkflow(id);
  const { data: status } = useWorkflowStatus(id);
  const { data: tasks } = useWorkflowTasks(id);
  const { data: walletBalance } = useQuery({
    queryKey: ["wallet-balance", id],
    queryFn: () => api.get<any>(`/wallet/workflow/${id}/balance`),
    enabled: !!id,
    refetchInterval: 5000,
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="animate-spin text-massclaw-accent" size={32} />
      </div>
    );
  }

  if (!workflow) return <div className="text-massclaw-text-muted">Workflow not found</div>;

  const result = workflow.result as Record<string, any> | null;
  const isTerminal = ["completed", "failed", "cancelled"].includes(workflow.status);

  return (
    <div className="space-y-6 max-w-5xl">
      {/* Header */}
      <div>
        <div className="flex items-center gap-3">
          <GitBranch size={22} className="text-massclaw-accent" />
          <h1 className="text-2xl font-bold">Workflow</h1>
          <span className={cn(
            "text-[11px] font-semibold px-2.5 py-1 rounded-full uppercase tracking-wider",
            workflow.status === "completed" && "bg-emerald-500/15 text-emerald-400",
            workflow.status === "failed" && "bg-red-500/15 text-red-400",
            ["pending", "decomposing", "running"].includes(workflow.status) && "bg-indigo-500/15 text-indigo-400 animate-pulse",
            workflow.status === "cancelled" && "bg-gray-500/15 text-gray-400",
          )}>
            {workflow.status}
          </span>
        </div>
        <p className="text-massclaw-text-muted mt-2 leading-relaxed max-w-3xl text-sm">{workflow.prompt}</p>
      </div>

      {/* Agent Plan — the main interactive component */}
      <AgentPlan
        tasks={tasks || []}
        workflowStatus={workflow.status}
        executionMode={workflow.execution_mode}
        domain={workflow.domain}
        progressPercent={status?.progress_percent || 0}
        budgetUsed={status?.budget_used || 0}
        budgetLimit={status?.budget_limit || 0}
        elapsedSeconds={status?.elapsed_seconds}
      />

      {/* Wallet (only when we have data) */}
      {walletBalance && walletBalance.budget_used > 0 && (
        <div className="relative overflow-hidden rounded-xl border border-white/[0.06]">
          <div className="absolute inset-0 bg-massclaw-surface" />
          <div className="relative p-5">
            <h2 className="text-sm font-semibold mb-3 flex items-center gap-2">
              <DollarSign size={16} className="text-amber-400" /> Wallet
            </h2>
            <div className="grid grid-cols-4 gap-4 text-sm">
              <div><p className="text-massclaw-text-muted text-xs">Budget</p><p className="font-medium">{formatCredits(walletBalance.budget_limit)}</p></div>
              <div><p className="text-massclaw-text-muted text-xs">Used</p><p className="font-medium">{formatCredits(walletBalance.budget_used)}</p></div>
              <div><p className="text-massclaw-text-muted text-xs">Remaining</p><p className="font-medium text-emerald-400">{formatCredits(walletBalance.budget_remaining)}</p></div>
              <div><p className="text-massclaw-text-muted text-xs">Utilization</p><p className="font-medium">{((walletBalance.budget_used / Math.max(walletBalance.budget_limit, 1)) * 100).toFixed(1)}%</p></div>
            </div>
          </div>
        </div>
      )}

      {/* Final Result */}
      {isTerminal && result?.content && (
        <div>
          <h2 className="text-lg font-semibold mb-3 flex items-center gap-2">
            <CheckCheck size={20} className="text-emerald-400" />
            Synthesized Result
          </h2>
          <div className="relative overflow-hidden rounded-xl border border-emerald-500/10">
            <div className="absolute inset-0 bg-gradient-to-br from-emerald-500/[0.02] to-transparent" />
            <div className="absolute inset-0 bg-massclaw-surface/95" />
            <div className="relative p-6">
              <div className="flex items-center gap-3 mb-4">
                {result.confidence != null && (
                  <span className={cn(
                    "text-[11px] font-semibold px-2.5 py-1 rounded-full",
                    result.confidence >= 0.8 ? "bg-emerald-500/15 text-emerald-400" :
                    result.confidence >= 0.5 ? "bg-amber-500/15 text-amber-400" :
                    "bg-red-500/15 text-red-400"
                  )}>
                    {(result.confidence * 100).toFixed(0)}% confidence
                  </span>
                )}
                {result.summary && <span className="text-xs text-massclaw-text-muted">{result.summary}</span>}
              </div>
              <div className="text-sm leading-7 text-massclaw-text">
                {renderContent(result.content)}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function renderContent(content: string): React.ReactNode {
  if (!content) return <p className="text-massclaw-text-muted italic">No content</p>;

  if (content.trim().startsWith("{") || content.trim().startsWith("[")) {
    try {
      const parsed = JSON.parse(content);
      if (parsed.tasks && Array.isArray(parsed.tasks)) {
        return (
          <div className="space-y-3">
            <p className="font-medium">Domain: <span className="text-massclaw-accent">{parsed.domain}</span></p>
            {parsed.tasks.map((t: any) => (
              <div key={t.id} className="bg-massclaw-bg rounded-lg p-3 border border-white/[0.04]">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-mono font-bold text-massclaw-accent">{t.id}</span>
                  <span className="text-sm font-medium capitalize">{t.capability?.replace(/-/g, " ")}</span>
                </div>
                <p className="text-xs text-massclaw-text-muted mt-1">{t.description}</p>
              </div>
            ))}
          </div>
        );
      }
      return <pre className="text-xs bg-massclaw-bg rounded-lg p-4 overflow-auto">{JSON.stringify(parsed, null, 2)}</pre>;
    } catch {}
  }

  return content.split("\n").map((line, i) => {
    if (line.startsWith("### ")) return <h3 key={i} className="text-base font-bold mt-5 mb-1.5">{line.slice(4)}</h3>;
    if (line.startsWith("## ")) return <h2 key={i} className="text-lg font-bold mt-6 mb-2">{line.slice(3)}</h2>;
    if (line.startsWith("# ")) return <h1 key={i} className="text-xl font-bold mt-6 mb-2">{line.slice(2)}</h1>;
    if (line.startsWith("- **") && line.includes("**")) {
      const match = line.match(/^- \*\*(.+?)\*\*:?\s*(.*)/);
      if (match) return <li key={i} className="ml-4 list-disc"><strong>{match[1]}</strong>{match[2] ? `: ${match[2]}` : ""}</li>;
    }
    if (line.startsWith("- ") || line.startsWith("* ")) return <li key={i} className="ml-4 list-disc text-massclaw-text-muted">{line.slice(2)}</li>;
    if (line.match(/^\d+\.\s/)) return <li key={i} className="ml-4 list-decimal">{line.replace(/^\d+\.\s/, "")}</li>;
    if (line.startsWith("**") && line.endsWith("**")) return <p key={i} className="font-bold mt-3">{line.slice(2, -2)}</p>;
    if (line.startsWith("---")) return <hr key={i} className="border-massclaw-border/50 my-4" />;
    if (line.trim() === "") return <div key={i} className="h-2" />;
    return <p key={i} className="text-massclaw-text-muted">{line}</p>;
  });
}
