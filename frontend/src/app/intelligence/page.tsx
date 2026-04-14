"use client";

import { Brain, RefreshCw, GitBranch, CheckCircle2, AlertTriangle, XCircle, ArrowRight, type LucideIcon } from "lucide-react";
import { useRecentWorkflows } from "@/hooks/useIntelligence";

function StatCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="glass rounded-xl p-4 flex flex-col gap-1">
      <span className="text-[10px] font-semibold uppercase tracking-widest text-massclaw-text-muted">{label}</span>
      <span className="text-2xl font-bold text-massclaw-text">{value}</span>
      {sub && <span className="text-xs text-massclaw-text-muted">{sub}</span>}
    </div>
  );
}

function CycleStep({
  icon: Icon,
  label,
  description,
  color,
  isLast = false,
}: {
  icon: LucideIcon;
  label: string;
  description: string;
  color: string;
  isLast?: boolean;
}) {
  return (
    <div className="flex items-start gap-3">
      <div className="flex flex-col items-center">
        <div className={`w-9 h-9 rounded-lg flex items-center justify-center ${color}`}>
          <Icon size={16} />
        </div>
        {!isLast && <div className="w-px h-8 bg-massclaw-border mt-1" />}
      </div>
      <div className="pt-1 pb-6">
        <p className="text-sm font-semibold text-massclaw-text">{label}</p>
        <p className="text-xs text-massclaw-text-muted mt-0.5">{description}</p>
      </div>
    </div>
  );
}

function ReflectionActionBadge({ action }: { action: string }) {
  const config: Record<string, { label: string; className: string; icon: LucideIcon }> = {
    accept: { label: "Accept", className: "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20", icon: CheckCircle2 },
    retry_task: { label: "Retry Task", className: "bg-amber-500/10 text-amber-400 border border-amber-500/20", icon: RefreshCw },
    re_plan: { label: "Re-Plan", className: "bg-violet-500/10 text-violet-400 border border-violet-500/20", icon: GitBranch },
    add_verifier: { label: "Add Verifier", className: "bg-blue-500/10 text-blue-400 border border-blue-500/20", icon: CheckCircle2 },
    abort: { label: "Abort", className: "bg-red-500/10 text-red-400 border border-red-500/20", icon: XCircle },
  };
  const c = config[action] ?? config.accept;
  const Icon = c.icon;
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-semibold ${c.className}`}>
      <Icon size={10} /> {c.label}
    </span>
  );
}

export default function IntelligencePage() {
  const { data: workflows, isLoading } = useRecentWorkflows();

  // Aggregate reflection stats from workflow task outputs
  const stats = (() => {
    if (!workflows?.items) return { total: 0, retries: 0, avgConfidence: null as number | null };
    let total = 0;
    let retries = 0;
    let confidenceSum = 0;
    let confidenceCount = 0;
    for (const wf of workflows.items) {
      for (const task of wf.tasks ?? []) {
        const reflection = task.output?.reflection;
        if (!reflection) continue;
        total++;
        if (task.output?.retry_count > 0) retries++;
        if (typeof reflection.confidence === "number") {
          confidenceSum += reflection.confidence;
          confidenceCount++;
        }
      }
    }
    return {
      total,
      retries,
      avgConfidence: confidenceCount > 0 ? confidenceSum / confidenceCount : null,
    };
  })();

  // Collect individual reflected tasks for the activity feed
  const reflectedTasks: Array<{
    workflowId: string;
    capability: string;
    reflection: { action: string; confidence: number; issues: string[] };
    retryCount: number;
  }> = [];

  if (workflows?.items) {
    for (const wf of workflows.items) {
      for (const task of wf.tasks ?? []) {
        if (task.output?.reflection) {
          reflectedTasks.push({
            workflowId: wf.workflow_id,
            capability: task.capability,
            reflection: task.output.reflection,
            retryCount: task.output.retry_count ?? 0,
          });
        }
      }
    }
  }

  return (
    <div className="min-h-screen bg-massclaw-bg text-massclaw-text p-6 pb-28">
      {/* Header */}
      <div className="flex items-center gap-3 mb-8">
        <div className="w-10 h-10 rounded-xl bg-massclaw-accent/10 border border-massclaw-accent/20 flex items-center justify-center">
          <Brain className="text-massclaw-accent" size={20} />
        </div>
        <div>
          <h1 className="text-xl font-bold">Adaptive Intelligence</h1>
          <p className="text-xs text-massclaw-text-muted">Reflection, self-correction, and dynamic replanning</p>
        </div>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-3 gap-3 mb-8">
        <StatCard
          label="Reflections"
          value={isLoading ? "—" : stats.total}
          sub="across recent workflows"
        />
        <StatCard
          label="Retries Triggered"
          value={isLoading ? "—" : stats.retries}
          sub="low-confidence tasks retried"
        />
        <StatCard
          label="Avg Confidence"
          value={
            isLoading
              ? "—"
              : stats.avgConfidence !== null
              ? `${(stats.avgConfidence * 100).toFixed(0)}%`
              : "N/A"
          }
          sub="mean reflection score"
        />
      </div>

      {/* How it works */}
      <div className="glass rounded-xl p-5 mb-6">
        <h2 className="text-sm font-semibold mb-4 flex items-center gap-2">
          <Brain size={14} className="text-massclaw-accent" />
          How Adaptive Intelligence Works
        </h2>
        <CycleStep
          icon={CheckCircle2}
          label="1. Post-Task Reflection"
          description="After every task completes, the ReflectionEngine evaluates output quality against the workflow goal. It returns an action: accept, retry_task, add_verifier, re_plan, or abort — along with a confidence score and specific issues."
          color="bg-emerald-500/10 text-emerald-400"
        />
        <CycleStep
          icon={RefreshCw}
          label="2. Self-Correction"
          description="When reflection confidence falls below 0.5 and the action is retry_task, the SelfCorrectionEngine builds an improved prompt incorporating identified issues and suggestions, then re-queues the task (max 2 retries)."
          color="bg-amber-500/10 text-amber-400"
        />
        <CycleStep
          icon={GitBranch}
          label="3. Dynamic Replanning"
          description="When reflection suggests re_plan, the DynamicReplanner adds a compensating alternative node to the DAG with a fundamentally different methodology. Both paths can contribute to the final synthesis."
          color="bg-violet-500/10 text-violet-400"
          isLast
        />
      </div>

      {/* Where to find reflection data */}
      <div className="glass rounded-xl p-5 mb-6 border border-massclaw-accent/10">
        <h2 className="text-sm font-semibold mb-2 flex items-center gap-2">
          <AlertTriangle size={14} className="text-massclaw-accent" />
          Where to Find Reflection Data
        </h2>
        <p className="text-xs text-massclaw-text-muted mb-3">
          Reflection results are stored in each task&apos;s output JSON under{" "}
          <code className="font-mono bg-white/5 px-1 py-0.5 rounded text-massclaw-accent">output.reflection</code>.
          Expand any completed task in a workflow to see the reflection badge showing action, confidence, and issues.
        </p>
        <div className="flex items-center gap-2 text-xs text-massclaw-accent">
          <ArrowRight size={12} />
          <span>Go to Missions, open a workflow, expand a task to view its reflection output</span>
        </div>
      </div>

      {/* Recent reflected tasks */}
      <div className="glass rounded-xl p-5">
        <h2 className="text-sm font-semibold mb-4">Recent Reflected Tasks</h2>
        {isLoading ? (
          <p className="text-xs text-massclaw-text-muted">Loading...</p>
        ) : reflectedTasks.length === 0 ? (
          <p className="text-xs text-massclaw-text-muted">
            No reflected tasks yet. Run a workflow to see adaptive intelligence in action.
          </p>
        ) : (
          <div className="space-y-2">
            {reflectedTasks.slice(0, 20).map((rt, i) => (
              <div
                key={i}
                className="flex items-start justify-between gap-3 rounded-lg bg-massclaw-bg/60 border border-white/[0.04] p-3"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-xs font-medium text-massclaw-text">
                      {rt.capability.replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}
                    </span>
                    <ReflectionActionBadge action={rt.reflection.action} />
                    {rt.retryCount > 0 && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-400">
                        retry #{rt.retryCount}
                      </span>
                    )}
                  </div>
                  {rt.reflection.issues.length > 0 && (
                    <p className="text-[10px] text-massclaw-text-muted truncate">
                      {rt.reflection.issues[0]}
                    </p>
                  )}
                </div>
                <span className="text-[10px] font-mono text-massclaw-text-muted flex-shrink-0">
                  {(rt.reflection.confidence * 100).toFixed(0)}%
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
