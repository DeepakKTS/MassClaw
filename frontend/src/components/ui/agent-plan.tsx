// @ts-nocheck — framer-motion v12 variant typing issues
"use client";

import React, { useState, useEffect, useRef, useMemo } from "react";
import {
  CheckCircle2, Circle, CircleAlert, CircleDotDashed, CircleX,
  Brain, Search, Zap, Sparkles, BarChart3, FileText, Shield,
  DollarSign, Timer, ChevronRight, Eye, Cpu,
} from "lucide-react";
import { motion, AnimatePresence, LayoutGroup } from "framer-motion";

interface WorkflowTask {
  task_id: string;
  step_number: number;
  capability: string;
  description?: string;
  status: string;
  assigned_agent_id?: string | null;
  confidence?: number | null;
  cost_used: number;
  latency_ms?: number | null;
  error_message?: string | null;
  output?: Record<string, any> | null;
}

interface AgentPlanProps {
  tasks: WorkflowTask[];
  workflowStatus: string;
  executionMode?: string | null;
  domain?: string | null;
  progressPercent?: number;
  budgetUsed?: number;
  budgetLimit?: number;
  elapsedSeconds?: number | null;
  dagSnapshot?: Record<string, unknown> | null;
}

// DAG node shape from backend
interface DAGNode {
  node_id: string;
  capability: string;
  description?: string;
  status?: string;
}

function mapStatus(status: string): string {
  const map: Record<string, string> = {
    completed: "completed", running: "in-progress", pending: "pending",
    assigned: "pending", todo: "pending", failed: "failed",
    skipped: "failed", blocked: "need-help", retrying: "in-progress",
  };
  return map[status] || "pending";
}

function getCapabilityIcon(capability: string) {
  const iconMap: Record<string, any> = {
    intake: Search, classify: Search, research: Brain,
    "data-retrieval": Brain, "literature-review": Brain,
    "process-analysis": BarChart3, "workflow-mapping": BarChart3,
    "bottleneck-detection": BarChart3, "risk-assessment": Shield,
    "compliance-check": Shield, "safety-analysis": Shield,
    optimization: Zap, scheduling: Zap,
    "resource-allocation": Zap, "extract-requirements": FileText,
    "cost-analysis": DollarSign, "budget-estimation": DollarSign,
    summarization: FileText, "report-generation": FileText,
    "executive-brief": FileText, verification: CheckCircle2,
    "quality-check": CheckCircle2,
  };
  return iconMap[capability] || Sparkles;
}

function formatCapability(cap: string): string {
  return cap.replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

const THINKING_STAGES = [
  { icon: Brain, label: "Interpreting goal", sublabel: "Analyzing intent & constraints", color: "text-indigo-400" },
  { icon: Search, label: "Decomposing ops", sublabel: "Building execution DAG", color: "text-violet-400" },
  { icon: Zap, label: "Selecting agents", sublabel: "Matching capabilities & trust", color: "text-amber-400" },
  { icon: Sparkles, label: "Agents executing", sublabel: "Running with shared context", color: "text-emerald-400" },
  { icon: BarChart3, label: "Analyzing outputs", sublabel: "Cross-checking results", color: "text-blue-400" },
  { icon: FileText, label: "Synthesizing report", sublabel: "Composing final response", color: "text-pink-400" },
];

const DEFAULT_NODES = [
  { icon: Brain, label: "Researcher" },
  { icon: Shield, label: "Sentinel" },
  { icon: Zap, label: "Optimizer" },
  { icon: Eye, label: "Inspector" },
  { icon: Search, label: "Analyzer" },
  { icon: FileText, label: "Reporter" },
];

const METALLIC = {
  silver: "#B8B8C0", gold: "#D4AF37", emerald: "#50C878", rose: "#B76E79",
  idle: "#4A4A52",
  lineDim: "rgba(120,120,130,0.06)", lineActive: "rgba(212,175,55,0.25)",
  lineComplete: "rgba(80,200,120,0.18)", lineFail: "rgba(183,110,121,0.15)",
};

const STAGE_ACTIVITIES: Record<number, string[]> = {
  0: ["Parsing mission briefing", "Extracting intent signals", "Identifying constraints", "Mapping objectives", "Classifying complexity", "Evaluating risk", "Building semantic context"],
  1: ["Generating task graph", "Computing DAG topology", "Estimating complexity", "Resolving capabilities", "Detecting parallelism", "Validating ordering", "Assigning priorities"],
  2: ["Querying agent registry", "Evaluating trust scores", "Matching capabilities", "Computing fitness", "Checking health", "Reserving budget", "Finalizing assignments"],
  3: ["Dispatching to agents", "Writing shared memory", "Monitoring heartbeats", "Collecting outputs", "Cross-referencing", "Recording trust", "Streaming progress"],
  4: ["Aggregating outputs", "Computing confidence", "Detecting conflicts", "Running consensus", "Validating integrity"],
  5: ["Composing synthesis", "Formatting summary", "Computing final score", "Generating audit trail"],
};

// ─── Main Component ──────────────────────────────────────────────
export default function AgentPlan({
  tasks, workflowStatus, executionMode, domain,
  progressPercent = 0, budgetUsed = 0, budgetLimit = 0, elapsedSeconds, dagSnapshot,
}: AgentPlanProps) {
  const [expandedTasks, setExpandedTasks] = useState<string[]>([]);
  const prefersReducedMotion = typeof window !== "undefined"
    ? window.matchMedia("(prefers-reduced-motion: reduce)").matches : false;

  const isActive = ["pending", "decomposing", "running"].includes(workflowStatus);
  const completedCount = tasks.filter((t) => t.status === "completed").length;
  const runningCount = tasks.filter((t) => t.status === "running").length;
  const totalCount = tasks.length;

  // Extract planned nodes from DAG snapshot
  const dagNodes: DAGNode[] = useMemo(() => {
    if (!dagSnapshot || !dagSnapshot.nodes) return [];
    return (dagSnapshot.nodes as any[]).map((n) => ({
      node_id: n.node_id, capability: n.capability,
      description: n.description, status: n.status,
    }));
  }, [dagSnapshot]);

  const [pendingTimer, setPendingTimer] = useState(0);
  useEffect(() => {
    if (workflowStatus === "pending" || (workflowStatus === "running" && totalCount === 0)) {
      const interval = setInterval(() => setPendingTimer((t) => t + 1), 2000);
      return () => clearInterval(interval);
    } else { setPendingTimer(0); }
  }, [workflowStatus, totalCount]);

  const getStageIndex = () => {
    if (workflowStatus === "completed" || workflowStatus === "failed") return 5;
    if (totalCount > 0 && completedCount > 0) {
      if (completedCount >= totalCount) return 5;
      if (completedCount >= totalCount * 0.7) return 4;
      return 3;
    }
    if (totalCount > 0 && runningCount > 0) return 3;
    if (totalCount > 0) return 2;
    if (pendingTimer >= 4) return 2;
    if (pendingTimer >= 2) return 1;
    return 0;
  };

  const toggleTaskExpansion = (taskId: string) => {
    setExpandedTasks((prev) => prev.includes(taskId) ? prev.filter((id) => id !== taskId) : [...prev, taskId]);
  };

  const taskVariants = {
    hidden: { opacity: 0, y: prefersReducedMotion ? 0 : -5 },
    visible: { opacity: 1, y: 0, transition: { type: (prefersReducedMotion ? "tween" : "spring") as "tween" | "spring", stiffness: 500, damping: 30 } },
  };
  const subtaskListVariants = {
    hidden: { opacity: 0, height: 0, overflow: "hidden" },
    visible: { height: "auto", opacity: 1, overflow: "visible", transition: { duration: 0.25, ease: [0.2, 0.65, 0.3, 0.9] } },
    exit: { height: 0, opacity: 0, overflow: "hidden", transition: { duration: 0.2, ease: [0.2, 0.65, 0.3, 0.9] } },
  };
  const statusBadgeVariants = {
    initial: { scale: 1 },
    animate: { scale: prefersReducedMotion ? 1 : [1, 1.08, 1], transition: { duration: 0.35, ease: [0.34, 1.56, 0.64, 1] } },
  };
  const budgetPercent = budgetLimit > 0 ? (budgetUsed / budgetLimit) * 100 : 0;

  return (
    <div className="text-massclaw-text">
      {isActive && (
        <ThinkingAnimation stageIndex={getStageIndex()} completedCount={completedCount} totalCount={totalCount} tasks={tasks} dagNodes={dagNodes} />
      )}

      {totalCount > 0 && (
        <motion.div className="mb-4 glass rounded-xl p-4" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0, transition: { duration: 0.3 } }}>
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-3">
              <span className="text-sm font-medium">{completedCount}/{totalCount} ops</span>
              {domain && <span className="text-[10px] px-2 py-0.5 rounded-full bg-massclaw-accent/10 text-massclaw-accent uppercase tracking-wider">{domain}</span>}
              {executionMode && <span className="text-[10px] px-2 py-0.5 rounded-full bg-violet-500/10 text-violet-400 uppercase tracking-wider">{executionMode.replace(/_/g, " ")}</span>}
            </div>
            <span className="text-sm font-bold text-massclaw-accent">{progressPercent}%</span>
          </div>
          <div className="w-full bg-white/[0.04] rounded-full h-1.5 overflow-hidden">
            <motion.div className="h-full rounded-full bg-massclaw-accent" initial={{ width: 0 }} animate={{ width: `${progressPercent}%` }} transition={{ duration: 0.8, ease: [0.2, 0.65, 0.3, 0.9] }} />
          </div>
          <div className="flex items-center justify-between mt-3">
            <div className="flex items-center gap-4 text-xs text-massclaw-text-muted">
              {budgetUsed > 0 && <span className="flex items-center gap-1"><DollarSign size={11} /> {budgetUsed.toFixed(1)} / {budgetLimit.toFixed(0)} cr</span>}
              {elapsedSeconds != null && <span className="flex items-center gap-1"><Timer size={11} /> {elapsedSeconds.toFixed(1)}s</span>}
            </div>
            {budgetLimit > 0 && (
              <div className="flex items-center gap-2">
                <div className="w-20 h-1 rounded-full bg-white/[0.06] overflow-hidden">
                  <div className={`h-full rounded-full transition-all duration-500 ${budgetPercent > 80 ? "bg-red-400" : budgetPercent > 50 ? "bg-amber-400" : "bg-emerald-400"}`} style={{ width: `${Math.min(budgetPercent, 100)}%` }} />
                </div>
                <span className="text-[10px] text-massclaw-text-muted font-mono">{budgetPercent.toFixed(0)}%</span>
              </div>
            )}
          </div>
        </motion.div>
      )}

      {totalCount > 0 && (
        <motion.div className="glass rounded-xl shadow overflow-hidden" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0, transition: { duration: 0.3, delay: 0.1 } }}>
          <LayoutGroup>
            <div className="p-3 overflow-hidden">
              <ul className="space-y-0.5 overflow-hidden">
                {tasks.map((task) => {
                  const planStatus = mapStatus(task.status);
                  const isExpanded = expandedTasks.includes(task.task_id);
                  const isCompleted = task.status === "completed";
                  const isRunning = task.status === "running";
                  const CapIcon = getCapabilityIcon(task.capability);
                  const output = task.output as Record<string, any> | null;
                  return (
                    <motion.li key={task.task_id} variants={taskVariants} initial="hidden" animate="visible" layout>
                      <motion.div className="group flex items-center px-3 py-2 rounded-lg cursor-pointer" onClick={() => toggleTaskExpansion(task.task_id)} whileHover={{ backgroundColor: "rgba(255,255,255,0.02)", transition: { duration: 0.2 } }}>
                        <div className="mr-3 flex-shrink-0">
                          <AnimatePresence mode="wait">
                            <motion.div key={task.status} initial={{ opacity: 0, scale: 0.8, rotate: -10 }} animate={{ opacity: 1, scale: 1, rotate: 0 }} exit={{ opacity: 0, scale: 0.8, rotate: 10 }} transition={{ duration: 0.2, ease: [0.2, 0.65, 0.3, 0.9] }}>
                              {planStatus === "completed" ? <CheckCircle2 className="h-[18px] w-[18px] text-emerald-500" /> : planStatus === "in-progress" ? <CircleDotDashed className="h-[18px] w-[18px] text-indigo-400 animate-spin" style={{ animationDuration: "3s" }} /> : planStatus === "need-help" ? <CircleAlert className="h-[18px] w-[18px] text-amber-400" /> : planStatus === "failed" ? <CircleX className="h-[18px] w-[18px] text-red-400" /> : <Circle className="h-[18px] w-[18px] text-massclaw-text-muted/40" />}
                            </motion.div>
                          </AnimatePresence>
                        </div>
                        <span className="mr-3 w-5 text-center text-xs font-mono text-massclaw-text-muted/60">{task.step_number}</span>
                        <CapIcon size={14} className="mr-2 text-massclaw-text-muted/50 flex-shrink-0" />
                        <div className="flex-1 min-w-0 mr-3">
                          <span className={`text-sm ${isCompleted ? "text-massclaw-text-muted line-through" : ""}`}>{formatCapability(task.capability)}</span>
                          {isRunning && <motion.span className="ml-2 text-[10px] text-indigo-400" animate={{ opacity: [0.4, 1, 0.4] }} transition={{ duration: 1.5, repeat: Infinity }}>executing...</motion.span>}
                        </div>
                        <div className="flex items-center gap-2 flex-shrink-0">
                          {task.latency_ms != null && <span className="text-[10px] text-massclaw-text-muted/50 font-mono">{task.latency_ms < 1000 ? `${Math.round(task.latency_ms)}ms` : `${(task.latency_ms / 1000).toFixed(1)}s`}</span>}
                          <motion.span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${planStatus === "completed" ? "bg-emerald-500/10 text-emerald-400" : planStatus === "in-progress" ? "bg-indigo-500/10 text-indigo-400" : planStatus === "need-help" ? "bg-amber-500/10 text-amber-400" : planStatus === "failed" ? "bg-red-500/10 text-red-400" : "bg-white/[0.04] text-massclaw-text-muted/60"}`} variants={statusBadgeVariants} initial="initial" animate="animate" key={task.status}>{task.status}</motion.span>
                          {output?.content && <ChevronRight size={14} className={`text-massclaw-text-muted/30 transition-transform duration-200 ${isExpanded ? "rotate-90" : ""}`} />}
                        </div>
                      </motion.div>
                      <AnimatePresence mode="wait">
                        {isExpanded && output?.content && (
                          <motion.div className="relative overflow-hidden" variants={subtaskListVariants} initial="hidden" animate="visible" exit="exit" layout>
                            <div className="absolute top-0 bottom-0 left-[26px] border-l border-dashed border-massclaw-text-muted/15" />
                            <div className="ml-10 mr-3 mb-3 mt-1">
                              <div className="rounded-lg bg-massclaw-bg/60 border border-white/[0.04] p-4">
                                <p className="text-[10px] font-semibold text-massclaw-text-muted/50 uppercase tracking-widest mb-2">Agent Output</p>
                                <div className="text-sm leading-relaxed text-massclaw-text-muted whitespace-pre-wrap max-h-80 overflow-auto">{output.content}</div>
                              </div>
                              {output.tool_calls && Array.isArray(output.tool_calls) && output.tool_calls.length > 0 && (
                                <div className="mt-3 space-y-2">
                                  <p className="text-[10px] font-semibold text-massclaw-text-muted/50 uppercase tracking-widest">
                                    Tool Calls ({output.tool_calls.length})
                                  </p>
                                  {output.tool_calls.map((tc: { tool: string; success: boolean; result: string; arguments?: Record<string, unknown> }, i: number) => (
                                    <div key={i} className="rounded-lg bg-massclaw-bg/40 border border-white/[0.04] p-3">
                                      <div className="flex items-center gap-2 mb-1">
                                        <span className="font-mono font-bold text-xs text-massclaw-accent">{tc.tool}</span>
                                        <span className={tc.success ? "text-massclaw-success text-xs" : "text-massclaw-danger text-xs"}>
                                          {tc.success ? "passed" : "failed"}
                                        </span>
                                      </div>
                                      {tc.arguments && (
                                        <div className="text-[10px] font-mono text-massclaw-text-muted/60 mb-1 truncate">
                                          {JSON.stringify(tc.arguments).slice(0, 120)}
                                        </div>
                                      )}
                                      <div className="text-xs text-massclaw-text-muted whitespace-pre-wrap max-h-32 overflow-auto">
                                        {tc.result}
                                      </div>
                                    </div>
                                  ))}
                                </div>
                              )}
                              {output.reflection && (
                                <div className="mt-2 rounded-lg bg-massclaw-accent/5 border border-massclaw-accent/20 p-3">
                                  <div className="flex items-center gap-2 mb-1">
                                    <span className="text-[10px] font-semibold text-massclaw-accent uppercase tracking-widest">Reflection</span>
                                    <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                                      output.reflection.action === "accept" ? "bg-massclaw-success/10 text-massclaw-success" :
                                      output.reflection.action === "retry_task" ? "bg-massclaw-warning/10 text-massclaw-warning" :
                                      "bg-massclaw-danger/10 text-massclaw-danger"
                                    }`}>
                                      {output.reflection.action}
                                    </span>
                                    <span className="text-[10px] text-massclaw-text-muted">
                                      confidence: {(output.reflection.confidence * 100).toFixed(0)}%
                                    </span>
                                    {output.retry_count && output.retry_count > 0 && (
                                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-massclaw-warning/10 text-massclaw-warning">
                                        retry #{output.retry_count}
                                      </span>
                                    )}
                                  </div>
                                  {output.reflection.issues && output.reflection.issues.length > 0 && (
                                    <div className="text-xs text-massclaw-text-muted mt-1">
                                      {output.reflection.issues.map((issue: string, i: number) => (
                                        <div key={i} className="flex items-start gap-1">
                                          <span className="text-massclaw-warning">•</span> {issue}
                                        </div>
                                      ))}
                                    </div>
                                  )}
                                </div>
                              )}
                              {task.error_message && <p className="text-xs text-red-400 mt-2 pl-1">{task.error_message}</p>}
                            </div>
                          </motion.div>
                        )}
                      </AnimatePresence>
                    </motion.li>
                  );
                })}
              </ul>
            </div>
          </LayoutGroup>
        </motion.div>
      )}
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────── */
/*  Reactive Agent Network — driven by real DAG + task state         */
/* ────────────────────────────────────────────────────────────────── */

const CX = 160;
const CY = 115;
const BASE_RADIUS = 80;

function getNodeColor(status: string): string {
  switch (status) {
    case "running": case "retrying": return METALLIC.gold;
    case "completed": return METALLIC.emerald;
    case "failed": case "skipped": return METALLIC.rose;
    case "pending": case "assigned": case "ready": return METALLIC.idle;
    default: return METALLIC.silver;
  }
}

function getLineColor(status: string): string {
  switch (status) {
    case "running": case "retrying": return METALLIC.lineActive;
    case "completed": return METALLIC.lineComplete;
    case "failed": case "skipped": return METALLIC.lineFail;
    default: return METALLIC.lineDim;
  }
}

interface VisNode {
  id: string;
  capability: string;
  label: string;
  icon: any;
  status: string; // "pending" | "running" | "completed" | "failed" | "idle"
  cost: number;
  latency: number | null;
  error: string | null;
}

function ThinkingAnimation({
  stageIndex, completedCount, totalCount, tasks, dagNodes,
}: {
  stageIndex: number; completedCount: number; totalCount: number;
  tasks: WorkflowTask[]; dagNodes: DAGNode[];
}) {
  const stage = THINKING_STAGES[stageIndex];
  const StageIcon = stage.icon;

  // Build visualization nodes from DAG snapshot + task status
  const visNodes: VisNode[] = useMemo(() => {
    // Priority 1: Use DAG snapshot nodes — these represent the PLANNED tasks
    if (dagNodes.length > 0) {
      return dagNodes.map((dn) => {
        // Find matching task by step number or capability
        const matchingTask = tasks.find((t) =>
          t.capability === dn.capability ||
          `t${t.step_number}` === dn.node_id
        );
        const status = matchingTask?.status || dn.status || "pending";
        return {
          id: dn.node_id,
          capability: dn.capability,
          label: formatCapability(dn.capability),
          icon: getCapabilityIcon(dn.capability),
          status,
          cost: matchingTask?.cost_used || 0,
          latency: matchingTask?.latency_ms || null,
          error: matchingTask?.error_message || null,
        };
      });
    }

    // Priority 2: Use actual tasks if they exist but no DAG snapshot
    if (tasks.length > 0) {
      return tasks.map((t) => ({
        id: t.task_id,
        capability: t.capability,
        label: formatCapability(t.capability),
        icon: getCapabilityIcon(t.capability),
        status: t.status,
        cost: t.cost_used,
        latency: t.latency_ms,
        error: t.error_message,
      }));
    }

    // Priority 3: No data yet — show default placeholder agents with ambient animation
    return DEFAULT_NODES.map((n, i) => ({
      id: `default-${i}`,
      capability: n.label.toLowerCase(),
      label: n.label,
      icon: n.icon,
      status: "idle" as string,
      cost: 0,
      latency: null as number | null,
      error: null as string | null,
    }));
  }, [dagNodes, tasks]);

  // Whether we have REAL data (DAG or tasks) vs placeholder defaults
  const hasRealData = dagNodes.length > 0 || tasks.length > 0;
  const anyRunning = visNodes.some((n) => n.status === "running" || n.status === "retrying");

  // Ambient cycling index — only for placeholder nodes
  const [ambientIdx, setAmbientIdx] = useState(0);
  useEffect(() => {
    if (hasRealData) return;
    const interval = setInterval(() => setAmbientIdx((i) => (i + 1) % visNodes.length), 2500);
    return () => clearInterval(interval);
  }, [hasRealData, visNodes.length]);

  const nodeCount = visNodes.length;
  const radius = nodeCount <= 4 ? 70 : nodeCount <= 6 ? 80 : nodeCount <= 8 ? 85 : 90;

  const nodePositions = visNodes.map((_, i) => {
    const angle = (Math.PI * 2 * i) / visNodes.length - Math.PI / 2;
    return { x: CX + radius * Math.cos(angle), y: CY + radius * Math.sin(angle) };
  });

  // Activity log driven by real task events
  const [activityLog, setActivityLog] = useState<string[]>([]);
  const prevSnapshotRef = useRef<string>("");
  const stageActivities = STAGE_ACTIVITIES[stageIndex] || STAGE_ACTIVITIES[0];

  // Real-time log from task status changes
  useEffect(() => {
    if (!hasRealData) return;
    const snapshot = visNodes.map((n) => `${n.capability}:${n.status}`).join("|");
    if (snapshot === prevSnapshotRef.current) return;
    prevSnapshotRef.current = snapshot;

    const entries: string[] = [];
    for (const n of visNodes) {
      if (n.status === "running") entries.push(`Running: ${n.label}`);
      else if (n.status === "completed" && n.latency) entries.push(`Done: ${n.label} — ${(n.latency / 1000).toFixed(1)}s`);
      else if (n.status === "failed") entries.push(`Failed: ${n.label}${n.error ? ` — ${n.error.slice(0, 35)}` : ""}`);
    }
    if (entries.length > 0) {
      setActivityLog((prev) => [...prev, ...entries].slice(-5));
    }
  }, [visNodes, hasRealData]);

  // Ambient log when placeholder mode
  useEffect(() => {
    if (hasRealData) return;
    let idx = 0;
    setActivityLog([stageActivities[0]]);
    const interval = setInterval(() => {
      idx = (idx + 1) % stageActivities.length;
      setActivityLog((prev) => [...prev.slice(-4), stageActivities[idx]]);
    }, 3500);
    return () => clearInterval(interval);
  }, [stageIndex, hasRealData]);

  const svgH = nodeCount > 8 ? 260 : 230;

  return (
    <motion.div
      className="relative overflow-hidden rounded-2xl mb-4 border border-white/[0.04]"
      style={{ background: "var(--mc-surface)" }}
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
    >
      <div className="p-5">
        {/* Header */}
        <div className="flex items-center justify-between mb-1">
          <div className="flex items-center gap-2.5">
            <motion.div
              className="w-8 h-8 rounded-lg flex items-center justify-center"
              style={{ background: "rgba(224,138,62,0.1)" }}
              animate={{ scale: anyRunning ? [1, 1.08, 1] : [1, 1.03, 1] }}
              transition={{ duration: anyRunning ? 1.5 : 3, repeat: Infinity, ease: "easeInOut" }}
            >
              <AnimatePresence mode="wait">
                <motion.div key={stageIndex} initial={{ opacity: 0, rotate: -20 }} animate={{ opacity: 1, rotate: 0 }} exit={{ opacity: 0, rotate: 20 }} transition={{ duration: 0.2 }}>
                  <StageIcon size={16} className={stage.color} />
                </motion.div>
              </AnimatePresence>
            </motion.div>
            <AnimatePresence mode="wait">
              <motion.div key={stageIndex} initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 8 }} transition={{ duration: 0.25 }}>
                <p className="text-xs font-semibold">{stage.label}</p>
                <p className="text-[10px] text-massclaw-text-muted/60">{stage.sublabel}</p>
              </motion.div>
            </AnimatePresence>
          </div>
          <div className="text-right">
            <ElapsedTimer />
            {hasRealData && <p className="text-[9px] text-massclaw-text-muted/40">{nodeCount} agents</p>}
          </div>
        </div>

        {/* Stage dots */}
        <div className="flex items-center gap-1 mb-4">
          {THINKING_STAGES.map((_, i) => (
            <motion.div key={i} className="rounded-full h-[3px]" animate={{ width: i === stageIndex ? 20 : 6, backgroundColor: i <= stageIndex ? "rgb(196,120,50)" : "rgba(255,255,255,0.06)" }} transition={{ duration: 0.3 }} />
          ))}
        </div>

        {/* ═══ Agent Network ═══ */}
        <div className="flex justify-center">
          <svg width="320" height={svgH} viewBox={`0 0 320 ${svgH}`} className="overflow-visible">
            <defs>
              <filter id="node-glow" x="-80%" y="-80%" width="260%" height="260%">
                <feGaussianBlur stdDeviation="4" result="blur" />
                <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
              </filter>
              <radialGradient id="hub-pulse">
                <stop offset="0%" stopColor={anyRunning ? "rgba(212,175,55,0.12)" : "rgba(224,138,62,0.08)"} />
                <stop offset="100%" stopColor="transparent" />
              </radialGradient>
            </defs>

            {/* Connections + particles */}
            {nodePositions.map((pos, i) => {
              const node = visNodes[i];
              const isRunning = hasRealData && (node.status === "running" || node.status === "retrying");
              const isComplete = hasRealData && node.status === "completed";
              const isFailed = hasRealData && (node.status === "failed" || node.status === "skipped");
              const isAmbientActive = !hasRealData && i === ambientIdx;
              const lineColor = hasRealData ? getLineColor(node.status) : (isAmbientActive ? "rgba(184,184,192,0.1)" : METALLIC.lineDim);

              return (
                <g key={`conn-${node.id}`}>
                  <line x1={pos.x} y1={pos.y} x2={CX} y2={CY} stroke={lineColor} strokeWidth={isRunning ? 1.5 : isComplete ? 1 : 0.5} />

                  {/* Particles for running tasks OR ambient active node */}
                  {(isRunning || isAmbientActive) && (
                    <>
                      <circle r="2.5" fill={isRunning ? METALLIC.gold : METALLIC.silver} opacity="0.9" filter="url(#node-glow)">
                        <animateMotion dur={isRunning ? "1.5s" : "3s"} repeatCount="indefinite" path={`M ${pos.x} ${pos.y} L ${CX} ${CY}`} />
                        <animate attributeName="opacity" values="0;0.9;0.9;0" dur={isRunning ? "1.5s" : "3s"} repeatCount="indefinite" />
                      </circle>
                      <circle r="1.5" fill={isRunning ? METALLIC.gold : METALLIC.silver} opacity="0.4">
                        <animateMotion dur={isRunning ? "2.2s" : "4s"} repeatCount="indefinite" begin="0.5s" path={`M ${CX} ${CY} L ${pos.x} ${pos.y}`} />
                        <animate attributeName="opacity" values="0;0.5;0.5;0" dur={isRunning ? "2.2s" : "4s"} repeatCount="indefinite" begin="0.5s" />
                      </circle>
                    </>
                  )}
                </g>
              );
            })}

            {/* Hub */}
            <circle cx={CX} cy={CY} r="35" fill="url(#hub-pulse)">
              <animate attributeName="r" values={anyRunning ? "28;36;28" : "30;34;30"} dur={anyRunning ? "2s" : "4s"} repeatCount="indefinite" />
            </circle>
            <circle cx={CX} cy={CY} r="22" fill="#0d0d15" stroke={anyRunning ? METALLIC.gold : "rgba(224,138,62,0.2)"} strokeWidth="1">
              {anyRunning && <animate attributeName="stroke-opacity" values="0.4;1;0.4" dur="1.5s" repeatCount="indefinite" />}
            </circle>
            <circle cx={CX} cy={CY} r="22" fill="none" stroke={anyRunning ? "rgba(212,175,55,0.06)" : "rgba(224,138,62,0.05)"} strokeWidth="6" />
            <foreignObject x={CX - 10} y={CY - 10} width="20" height="20">
              <div className="w-5 h-5 flex items-center justify-center">
                <Cpu size={14} style={{ color: anyRunning ? METALLIC.gold : "#E08A3E" }} />
              </div>
            </foreignObject>
            <text x={CX} y={CY + 34} textAnchor="middle" fill="rgba(148,163,184,0.4)" fontSize="7" fontFamily="system-ui" fontWeight="500">
              {hasRealData ? `${nodeCount} OPS` : "ORCHESTRATOR"}
            </text>

            {/* Agent Nodes */}
            {nodePositions.map((pos, i) => {
              const node = visNodes[i];
              const NodeIcon = node.icon;
              const isRunning = hasRealData && (node.status === "running" || node.status === "retrying");
              const isComplete = hasRealData && node.status === "completed";
              const isFailed = hasRealData && (node.status === "failed" || node.status === "skipped");
              const isAmbientActive = !hasRealData && i === ambientIdx;
              const nodeColor = hasRealData ? getNodeColor(node.status) : (isAmbientActive ? METALLIC.silver : METALLIC.idle);

              const isGlowing = isRunning || isAmbientActive;

              return (
                <g key={`node-${node.id}`}>
                  {isGlowing && (
                    <circle cx={pos.x} cy={pos.y} r="20" fill="none" stroke={nodeColor} strokeWidth="1" opacity="0.4" filter="url(#node-glow)">
                      <animate attributeName="r" values="17;22;17" dur="2s" repeatCount="indefinite" />
                      <animate attributeName="opacity" values="0.15;0.45;0.15" dur="2s" repeatCount="indefinite" />
                    </circle>
                  )}
                  {isComplete && (
                    <circle cx={pos.x} cy={pos.y} r="18" fill="none" stroke={METALLIC.emerald} strokeWidth="1" opacity="0.3" />
                  )}
                  <circle cx={pos.x} cy={pos.y} r="16" fill="#0d0d15"
                    stroke={nodeColor} strokeWidth={isGlowing ? 1.5 : isFailed ? 1 : 0.5}
                    strokeDasharray={isFailed ? "3 2" : "none"}
                  >
                    {isGlowing && <animate attributeName="stroke-opacity" values="0.5;1;0.5" dur="1.5s" repeatCount="indefinite" />}
                  </circle>
                  <foreignObject x={pos.x - 8} y={pos.y - 8} width="16" height="16">
                    <div className="w-4 h-4 flex items-center justify-center">
                      <NodeIcon size={11} style={{ color: isGlowing || isComplete ? nodeColor : isFailed ? METALLIC.rose : "rgba(148,163,184,0.3)" }} />
                    </div>
                  </foreignObject>
                  <text x={pos.x} y={pos.y + 24} textAnchor="middle"
                    fill={isGlowing || isComplete ? nodeColor : isFailed ? METALLIC.rose : "rgba(148,163,184,0.25)"}
                    fontSize="5.5" fontFamily="system-ui" fontWeight={isGlowing ? "600" : "400"}
                  >
                    {node.label.length > 16 ? node.label.slice(0, 16) : node.label}
                  </text>
                  {hasRealData && (
                    <circle cx={pos.x} cy={pos.y + 30} r="1.5"
                      fill={isComplete ? METALLIC.emerald : isRunning ? METALLIC.gold : isFailed ? METALLIC.rose : "rgba(74,74,82,0.4)"}
                    >
                      {isRunning && <animate attributeName="opacity" values="0.4;1;0.4" dur="1s" repeatCount="indefinite" />}
                    </circle>
                  )}
                </g>
              );
            })}
          </svg>
        </div>

        {/* Activity log */}
        <div className="mt-3 rounded-lg p-3 font-mono text-[10px] bg-massclaw-bg/30">
          <div className="flex items-center gap-1.5 mb-1.5">
            <div className="w-1 h-1 rounded-full bg-emerald-400 animate-pulse" />
            <span className="text-[8px] text-emerald-400/70 uppercase tracking-widest font-semibold">live</span>
          </div>
          <div className="space-y-0.5">
            {activityLog.map((msg, i) => {
              const isLatest = i === activityLog.length - 1;
              const isFail = msg.startsWith("Failed:");
              return (
                <motion.div key={`${i}-${msg}`} initial={{ opacity: 0, x: -5 }} animate={{ opacity: isLatest ? 0.9 : 0.25, x: 0 }} transition={{ duration: 0.2 }} className="flex items-center gap-1.5">
                  <span className="text-massclaw-text-muted/20 w-3 text-right shrink-0">{String(i + 1).padStart(2, "0")}</span>
                  <span className={isLatest ? (isFail ? "text-red-400/80" : "text-massclaw-text-muted") : "text-massclaw-text-muted/30"}>{msg}</span>
                  {isLatest && <motion.span className="text-massclaw-accent/60" animate={{ opacity: [1, 0, 1] }} transition={{ duration: 1, repeat: Infinity }}>|</motion.span>}
                </motion.div>
              );
            })}
          </div>
        </div>

        {totalCount > 0 && (
          <p className="text-[10px] text-massclaw-text-muted/40 mt-2 text-center">{completedCount}/{totalCount} ops completed</p>
        )}
      </div>
    </motion.div>
  );
}

function ElapsedTimer() {
  const [seconds, setSeconds] = React.useState(0);
  React.useEffect(() => {
    const interval = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(interval);
  }, []);
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return <p className="text-[10px] font-mono text-massclaw-text-muted/40">{mins > 0 ? `${mins}m ${String(secs).padStart(2, "0")}s` : `${secs}s`}</p>;
}
