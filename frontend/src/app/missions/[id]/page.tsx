"use client";

import { useParams } from "next/navigation";
import { useWorkflow, useWorkflowStatus, useWorkflowTasks } from "@/hooks/useWorkflows";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { formatCredits, cn } from "@/lib/utils";
import { Crosshair, DollarSign, CheckCheck, Loader2, ArrowLeft, Coins } from "lucide-react";
import { motion } from "framer-motion";
import AgentPlan from "@/components/ui/agent-plan";
import Link from "next/link";

export default function MissionDetailPage() {
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

  if (!workflow)
    return <div className="text-massclaw-text-muted text-center py-16">Mission not found</div>;

  const result = workflow.result as Record<string, any> | null;
  const isTerminal = ["completed", "failed", "cancelled"].includes(workflow.status);
  const isActive = ["pending", "decomposing", "running"].includes(workflow.status);

  return (
    <div className="max-w-[1200px] mx-auto py-4">
      {/* Top bar */}
      <div className="flex items-center justify-between mb-5">
        <Link href="/" className="inline-flex items-center gap-1.5 text-xs text-massclaw-text-muted hover:text-massclaw-text transition-colors">
          <ArrowLeft size={12} /> Mission Control
        </Link>
        <span className={cn(
          "text-[10px] font-semibold px-2 py-0.5 rounded-full uppercase tracking-wider",
          workflow.status === "completed" && "bg-emerald-500/15 text-emerald-400",
          workflow.status === "failed" && "bg-red-500/15 text-red-400",
          isActive && "bg-indigo-500/15 text-indigo-400 animate-pulse",
          workflow.status === "cancelled" && "bg-gray-500/15 text-gray-400"
        )}>
          {workflow.status}
        </span>
      </div>

      {/* ═══ Split Panel ═══ */}
      <div className="flex gap-5 min-h-[calc(100vh-10rem)]">

        {/* Left: Briefing */}
        <motion.aside
          initial={{ opacity: 0, x: -16 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.35 }}
          className="w-[280px] shrink-0 hidden lg:block"
        >
          <div className="sticky top-20 space-y-3">
            {/* Prompt */}
            <div className="rounded-2xl border border-white/[0.04] p-4" style={{ background: "var(--mc-surface)", backdropFilter: "blur(20px)" }}>
              <div className="flex items-center gap-2 mb-3">
                <Crosshair size={13} className="text-massclaw-accent" />
                <span className="text-[10px] font-semibold text-massclaw-text-muted/60 uppercase tracking-widest">Briefing</span>
              </div>
              <p className="text-[13px] text-massclaw-text leading-relaxed">{workflow.prompt}</p>
              {workflow.domain && (
                <div className="mt-3 pt-3 border-t border-white/[0.04]">
                  <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-massclaw-accent/10 text-massclaw-accent">{workflow.domain}</span>
                </div>
              )}
            </div>

            {/* Budget */}
            {walletBalance && (
              <div className="rounded-2xl border border-white/[0.04] p-4" style={{ background: "var(--mc-surface)", backdropFilter: "blur(20px)" }}>
                <div className="flex items-center gap-2 mb-2">
                  <Coins size={11} className="text-amber-400/60" />
                  <span className="text-[10px] font-semibold text-massclaw-text-muted/60 uppercase tracking-widest">Budget</span>
                </div>
                <div className="space-y-1.5 text-[11px]">
                  <div className="flex justify-between">
                    <span className="text-massclaw-text-muted/50">Used</span>
                    <span className="font-mono">{walletBalance.budget_used?.toFixed(1)} cr</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-massclaw-text-muted/50">Limit</span>
                    <span className="font-mono text-massclaw-text-muted/50">{walletBalance.budget_limit?.toFixed(0)} cr</span>
                  </div>
                  <div className="h-1 rounded-full bg-white/[0.04] overflow-hidden mt-1">
                    <div className={`h-full rounded-full transition-all duration-500 ${
                      (walletBalance.budget_used / Math.max(walletBalance.budget_limit, 1)) > 0.8 ? "bg-red-400/60" : "bg-massclaw-accent/40"
                    }`} style={{ width: `${Math.min((walletBalance.budget_used / Math.max(walletBalance.budget_limit, 1)) * 100, 100)}%` }} />
                  </div>
                </div>
                {isTerminal && walletBalance.budget_used > 0 && (
                  <p className="text-[9px] text-massclaw-text-muted/40 mt-2 leading-relaxed">
                    {(() => {
                      const util = (walletBalance.budget_used / Math.max(walletBalance.budget_limit, 1)) * 100;
                      if (util < 40) return `~${Math.max(50, Math.round(walletBalance.budget_used * 1.15 / 50) * 50)} cr would suffice next time.`;
                      if (util > 90) return `Consider ${Math.round(walletBalance.budget_used * 1.3 / 50) * 50} cr next time.`;
                      return "Budget well-sized.";
                    })()}
                  </p>
                )}
              </div>
            )}
          </div>
        </motion.aside>

        {/* Right: Operations */}
        <div className="flex-1 min-w-0 space-y-5">
          {/* Mobile prompt */}
          <div className="lg:hidden mb-4">
            <p className="text-sm text-massclaw-text-muted leading-relaxed">{workflow.prompt}</p>
          </div>

          {/* Agent Network + Task List (single component — no duplication) */}
          <AgentPlan
            tasks={tasks || []}
            workflowStatus={workflow.status}
            executionMode={workflow.execution_mode}
            domain={workflow.domain}
            progressPercent={status?.progress_percent || 0}
            budgetUsed={status?.budget_used || 0}
            budgetLimit={status?.budget_limit || 0}
            elapsedSeconds={status?.elapsed_seconds}
            dagSnapshot={workflow.dag_snapshot}
          />

          {/* Synthesized Result */}
          {isTerminal && result?.content && (
            <motion.div
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.4, delay: 0.15 }}
            >
              <div className="flex items-center gap-2 mb-3">
                <CheckCheck size={16} className="text-emerald-400" />
                <span className="text-sm font-semibold">Synthesized Result</span>
                {result.confidence != null && (
                  <span className={cn(
                    "text-[10px] font-semibold px-2 py-0.5 rounded-full",
                    result.confidence >= 0.8 ? "bg-emerald-500/15 text-emerald-400" :
                    result.confidence >= 0.5 ? "bg-amber-500/15 text-amber-400" : "bg-red-500/15 text-red-400"
                  )}>
                    {(result.confidence * 100).toFixed(0)}%
                  </span>
                )}
              </div>
              <div className="rounded-2xl border border-emerald-500/8 p-5 relative overflow-hidden" style={{ background: "var(--mc-surface)" }}>
                <div className="absolute inset-0 bg-gradient-to-br from-emerald-500/[0.02] to-transparent pointer-events-none" />
                <div className="relative text-sm leading-7 text-massclaw-text">
                  {renderContent(result.content)}
                </div>
              </div>
            </motion.div>
          )}
        </div>
      </div>
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
    if (line.startsWith("### ")) return <h3 key={i} className="text-base font-semibold mt-5 mb-1.5">{line.slice(4)}</h3>;
    if (line.startsWith("## ")) return <h2 key={i} className="text-lg font-semibold mt-6 mb-2">{line.slice(3)}</h2>;
    if (line.startsWith("# ")) return <h1 key={i} className="text-xl font-semibold mt-6 mb-2">{line.slice(2)}</h1>;
    if (line.startsWith("- **") && line.includes("**")) {
      const match = line.match(/^- \*\*(.+?)\*\*:?\s*(.*)/);
      if (match) return <li key={i} className="ml-4 list-disc"><strong>{match[1]}</strong>{match[2] ? `: ${match[2]}` : ""}</li>;
    }
    if (line.startsWith("- ") || line.startsWith("* ")) return <li key={i} className="ml-4 list-disc text-massclaw-text-muted">{line.slice(2)}</li>;
    if (line.match(/^\d+\.\s/)) return <li key={i} className="ml-4 list-decimal">{line.replace(/^\d+\.\s/, "")}</li>;
    if (line.startsWith("**") && line.endsWith("**")) return <p key={i} className="font-semibold mt-3">{line.slice(2, -2)}</p>;
    if (line.startsWith("---")) return <hr key={i} className="border-massclaw-border/50 my-4" />;
    if (line.trim() === "") return <div key={i} className="h-2" />;
    return <p key={i} className="text-massclaw-text-muted">{line}</p>;
  });
}
