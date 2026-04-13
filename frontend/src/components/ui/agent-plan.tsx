"use client";

import React, { useState } from "react";
import {
  CheckCircle2,
  Circle,
  CircleAlert,
  CircleDotDashed,
  CircleX,
  Brain,
  Search,
  Zap,
  Sparkles,
  BarChart3,
  FileText,
  Shield,
  DollarSign,
  Timer,
  ChevronRight,
} from "lucide-react";
import { motion, AnimatePresence, LayoutGroup } from "framer-motion";

// Type definitions matching MassClaw workflow data
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
}

// Map MassClaw task status to plan status
function mapStatus(status: string): string {
  const map: Record<string, string> = {
    completed: "completed",
    running: "in-progress",
    pending: "pending",
    assigned: "pending",
    todo: "pending",
    failed: "failed",
    skipped: "failed",
    blocked: "need-help",
    retrying: "in-progress",
  };
  return map[status] || "pending";
}

// Capability icons
function getCapabilityIcon(capability: string) {
  const iconMap: Record<string, any> = {
    intake: Search,
    classify: Search,
    research: Brain,
    "data-retrieval": Brain,
    "literature-review": Brain,
    "process-analysis": BarChart3,
    "workflow-mapping": BarChart3,
    "bottleneck-detection": BarChart3,
    "risk-assessment": Shield,
    "compliance-check": Shield,
    optimization: Zap,
    scheduling: Zap,
    "resource-allocation": Zap,
    "cost-analysis": DollarSign,
    "budget-estimation": DollarSign,
    summarization: FileText,
    "report-generation": FileText,
    "executive-brief": FileText,
    verification: CheckCircle2,
    "quality-check": CheckCircle2,
  };
  return iconMap[capability] || Sparkles;
}

// Thinking stages for the loading animation
const THINKING_STAGES = [
  { icon: Brain, label: "Interpreting goal", sublabel: "Analyzing intent & constraints", color: "text-indigo-400" },
  { icon: Search, label: "Decomposing tasks", sublabel: "Creating execution plan", color: "text-violet-400" },
  { icon: Zap, label: "Selecting agents", sublabel: "Matching capabilities & trust", color: "text-amber-400" },
  { icon: Sparkles, label: "Agents thinking", sublabel: "Executing with shared context", color: "text-emerald-400" },
  { icon: BarChart3, label: "Analyzing outputs", sublabel: "Cross-checking results", color: "text-blue-400" },
  { icon: FileText, label: "Synthesizing report", sublabel: "Composing final response", color: "text-pink-400" },
];

export default function AgentPlan({
  tasks,
  workflowStatus,
  executionMode,
  domain,
  progressPercent = 0,
  budgetUsed = 0,
  budgetLimit = 0,
  elapsedSeconds,
}: AgentPlanProps) {
  const [expandedTasks, setExpandedTasks] = useState<string[]>([]);
  const prefersReducedMotion =
    typeof window !== "undefined"
      ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
      : false;

  const isActive = ["pending", "decomposing", "running"].includes(workflowStatus);
  const completedCount = tasks.filter((t) => t.status === "completed").length;
  const runningCount = tasks.filter((t) => t.status === "running").length;
  const totalCount = tasks.length;

  // Time-based stage advancement for PENDING (before tasks appear)
  const [pendingTimer, setPendingTimer] = useState(0);
  React.useEffect(() => {
    if (workflowStatus === "pending" || (workflowStatus === "running" && totalCount === 0)) {
      const interval = setInterval(() => setPendingTimer((t) => t + 1), 2000);
      return () => clearInterval(interval);
    } else {
      setPendingTimer(0);
    }
  }, [workflowStatus, totalCount]);

  // Determine current thinking stage — advances with real data or time
  const getStageIndex = () => {
    if (workflowStatus === "completed" || workflowStatus === "failed") return 5;
    if (totalCount > 0 && completedCount > 0) {
      if (completedCount >= totalCount) return 5;
      if (completedCount >= totalCount * 0.7) return 4;
      return 3;
    }
    if (totalCount > 0 && runningCount > 0) return 3;
    if (totalCount > 0) return 2;
    // No tasks yet — advance based on time
    if (pendingTimer >= 4) return 2; // ~8s: "Selecting agents"
    if (pendingTimer >= 2) return 1; // ~4s: "Decomposing"
    return 0; // "Interpreting goal"
  };

  const toggleTaskExpansion = (taskId: string) => {
    setExpandedTasks((prev) =>
      prev.includes(taskId) ? prev.filter((id) => id !== taskId) : [...prev, taskId]
    );
  };

  // Animation variants
  const taskVariants = {
    hidden: { opacity: 0, y: prefersReducedMotion ? 0 : -5 },
    visible: {
      opacity: 1,
      y: 0,
      transition: {
        type: prefersReducedMotion ? "tween" : "spring",
        stiffness: 500,
        damping: 30,
      },
    },
  };

  const subtaskListVariants = {
    hidden: { opacity: 0, height: 0, overflow: "hidden" },
    visible: {
      height: "auto",
      opacity: 1,
      overflow: "visible",
      transition: {
        duration: 0.25,
        ease: [0.2, 0.65, 0.3, 0.9],
      },
    },
    exit: {
      height: 0,
      opacity: 0,
      overflow: "hidden",
      transition: { duration: 0.2, ease: [0.2, 0.65, 0.3, 0.9] },
    },
  };

  const statusBadgeVariants = {
    initial: { scale: 1 },
    animate: {
      scale: prefersReducedMotion ? 1 : [1, 1.08, 1],
      transition: { duration: 0.35, ease: [0.34, 1.56, 0.64, 1] },
    },
  };

  return (
    <div className="text-massclaw-text">
      {/* Thinking animation for active workflows */}
      {isActive && (
        <ThinkingAnimation
          stageIndex={getStageIndex()}
          completedCount={completedCount}
          totalCount={totalCount}
          workflowStatus={workflowStatus}
        />
      )}

      {/* Progress bar */}
      {totalCount > 0 && (
        <motion.div
          className="mb-4 rounded-xl border border-white/[0.06] bg-massclaw-surface p-4"
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0, transition: { duration: 0.3 } }}
        >
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-3">
              <span className="text-sm font-medium">
                {completedCount}/{totalCount} tasks
              </span>
              {domain && (
                <span className="text-[10px] px-2 py-0.5 rounded-full bg-massclaw-accent/10 text-massclaw-accent uppercase tracking-wider">
                  {domain}
                </span>
              )}
              {executionMode && (
                <span className="text-[10px] px-2 py-0.5 rounded-full bg-violet-500/10 text-violet-400 uppercase tracking-wider">
                  {executionMode.replace(/_/g, " ")}
                </span>
              )}
            </div>
            <span className="text-sm font-bold text-massclaw-accent">{progressPercent}%</span>
          </div>
          <div className="w-full bg-massclaw-border/30 rounded-full h-1.5 overflow-hidden">
            <motion.div
              className="h-full rounded-full bg-gradient-to-r from-massclaw-accent via-violet-500 to-emerald-500"
              initial={{ width: 0 }}
              animate={{ width: `${progressPercent}%` }}
              transition={{ duration: 0.8, ease: [0.2, 0.65, 0.3, 0.9] }}
            />
          </div>
          <div className="flex items-center gap-6 mt-3 text-xs text-massclaw-text-muted">
            {budgetUsed > 0 && (
              <span className="flex items-center gap-1">
                <DollarSign size={11} /> {budgetUsed.toFixed(1)} / {budgetLimit.toFixed(0)} credits
              </span>
            )}
            {elapsedSeconds != null && (
              <span className="flex items-center gap-1">
                <Timer size={11} /> {elapsedSeconds.toFixed(1)}s
              </span>
            )}
          </div>
        </motion.div>
      )}

      {/* Task list */}
      {totalCount > 0 && (
        <motion.div
          className="rounded-xl border border-white/[0.06] bg-massclaw-surface shadow overflow-hidden"
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0, transition: { duration: 0.3, delay: 0.1 } }}
        >
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
                    <motion.li
                      key={task.task_id}
                      variants={taskVariants}
                      initial="hidden"
                      animate="visible"
                      layout
                    >
                      <motion.div
                        className="group flex items-center px-3 py-2 rounded-lg cursor-pointer"
                        onClick={() => toggleTaskExpansion(task.task_id)}
                        whileHover={{
                          backgroundColor: "rgba(255,255,255,0.02)",
                          transition: { duration: 0.2 },
                        }}
                      >
                        {/* Status icon */}
                        <div className="mr-3 flex-shrink-0">
                          <AnimatePresence mode="wait">
                            <motion.div
                              key={task.status}
                              initial={{ opacity: 0, scale: 0.8, rotate: -10 }}
                              animate={{ opacity: 1, scale: 1, rotate: 0 }}
                              exit={{ opacity: 0, scale: 0.8, rotate: 10 }}
                              transition={{ duration: 0.2, ease: [0.2, 0.65, 0.3, 0.9] }}
                            >
                              {planStatus === "completed" ? (
                                <CheckCircle2 className="h-[18px] w-[18px] text-emerald-500" />
                              ) : planStatus === "in-progress" ? (
                                <CircleDotDashed className="h-[18px] w-[18px] text-indigo-400 animate-spin" style={{ animationDuration: "3s" }} />
                              ) : planStatus === "need-help" ? (
                                <CircleAlert className="h-[18px] w-[18px] text-amber-400" />
                              ) : planStatus === "failed" ? (
                                <CircleX className="h-[18px] w-[18px] text-red-400" />
                              ) : (
                                <Circle className="h-[18px] w-[18px] text-massclaw-text-muted/40" />
                              )}
                            </motion.div>
                          </AnimatePresence>
                        </div>

                        {/* Step number */}
                        <span className="mr-3 w-5 text-center text-xs font-mono text-massclaw-text-muted/60">
                          {task.step_number}
                        </span>

                        {/* Capability icon + name */}
                        <CapIcon size={14} className="mr-2 text-massclaw-text-muted/50 flex-shrink-0" />
                        <div className="flex-1 min-w-0 mr-3">
                          <span className={`text-sm ${isCompleted ? "text-massclaw-text-muted line-through" : ""}`}>
                            {task.capability.replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}
                          </span>
                          {isRunning && (
                            <motion.span
                              className="ml-2 text-[10px] text-indigo-400"
                              animate={{ opacity: [0.4, 1, 0.4] }}
                              transition={{ duration: 1.5, repeat: Infinity }}
                            >
                              thinking...
                            </motion.span>
                          )}
                        </div>

                        {/* Metrics + status badge */}
                        <div className="flex items-center gap-2 flex-shrink-0">
                          {task.latency_ms != null && (
                            <span className="text-[10px] text-massclaw-text-muted/50 font-mono">
                              {task.latency_ms < 1000 ? `${Math.round(task.latency_ms)}ms` : `${(task.latency_ms / 1000).toFixed(1)}s`}
                            </span>
                          )}
                          <motion.span
                            className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${
                              planStatus === "completed"
                                ? "bg-emerald-500/10 text-emerald-400"
                                : planStatus === "in-progress"
                                  ? "bg-indigo-500/10 text-indigo-400"
                                  : planStatus === "need-help"
                                    ? "bg-amber-500/10 text-amber-400"
                                    : planStatus === "failed"
                                      ? "bg-red-500/10 text-red-400"
                                      : "bg-massclaw-border text-massclaw-text-muted/60"
                            }`}
                            variants={statusBadgeVariants}
                            initial="initial"
                            animate="animate"
                            key={task.status}
                          >
                            {task.status}
                          </motion.span>
                          {output?.content && (
                            <ChevronRight
                              size={14}
                              className={`text-massclaw-text-muted/30 transition-transform duration-200 ${isExpanded ? "rotate-90" : ""}`}
                            />
                          )}
                        </div>
                      </motion.div>

                      {/* Expanded output */}
                      <AnimatePresence mode="wait">
                        {isExpanded && output?.content && (
                          <motion.div
                            className="relative overflow-hidden"
                            variants={subtaskListVariants}
                            initial="hidden"
                            animate="visible"
                            exit="exit"
                            layout
                          >
                            <div className="absolute top-0 bottom-0 left-[26px] border-l border-dashed border-massclaw-text-muted/15" />
                            <div className="ml-10 mr-3 mb-3 mt-1">
                              <div className="rounded-lg bg-massclaw-bg/60 border border-white/[0.04] p-4">
                                <p className="text-[10px] font-semibold text-massclaw-text-muted/50 uppercase tracking-widest mb-2">
                                  Agent Output
                                </p>
                                <div className="text-sm leading-relaxed text-massclaw-text-muted whitespace-pre-wrap max-h-80 overflow-auto">
                                  {output.content}
                                </div>
                              </div>
                              {task.error_message && (
                                <p className="text-xs text-red-400 mt-2 pl-1">{task.error_message}</p>
                              )}
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

/* ------------------------------------------------------------------ */
/*  Thinking animation component                                       */
/* ------------------------------------------------------------------ */

function ThinkingAnimation({
  stageIndex,
  completedCount,
  totalCount,
  workflowStatus,
}: {
  stageIndex: number;
  completedCount: number;
  totalCount: number;
  workflowStatus: string;
}) {
  const stage = THINKING_STAGES[stageIndex];
  const Icon = stage.icon;

  return (
    <motion.div
      className="relative overflow-hidden rounded-2xl border border-white/[0.06] p-8 mb-4"
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: [0.2, 0.65, 0.3, 0.9] }}
    >
      {/* Glass morphism background */}
      <div className="absolute inset-0 bg-gradient-to-br from-indigo-500/[0.04] via-purple-500/[0.03] to-pink-500/[0.04]" />
      <div className="absolute inset-0 bg-massclaw-surface/90 backdrop-blur-xl" />

      {/* Animated gradient orbs */}
      <motion.div
        className="absolute -top-20 -left-20 w-60 h-60 bg-indigo-500/[0.07] rounded-full blur-3xl"
        animate={{ scale: [1, 1.2, 1], opacity: [0.5, 0.8, 0.5] }}
        transition={{ duration: 4, repeat: Infinity, ease: "easeInOut" }}
      />
      <motion.div
        className="absolute -bottom-20 -right-20 w-60 h-60 bg-purple-500/[0.07] rounded-full blur-3xl"
        animate={{ scale: [1.2, 1, 1.2], opacity: [0.5, 0.8, 0.5] }}
        transition={{ duration: 4, repeat: Infinity, ease: "easeInOut", delay: 1 }}
      />

      <div className="relative flex flex-col items-center text-center space-y-4">
        {/* Spinning icon with glow */}
        <motion.div
          className="relative w-14 h-14 rounded-2xl border border-white/[0.08] flex items-center justify-center bg-gradient-to-br from-white/[0.06] to-white/[0.01]"
          animate={{ rotate: [0, 5, -5, 0] }}
          transition={{ duration: 4, repeat: Infinity, ease: "easeInOut" }}
        >
          <AnimatePresence mode="wait">
            <motion.div
              key={stageIndex}
              initial={{ opacity: 0, scale: 0.5 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.5 }}
              transition={{ duration: 0.3 }}
            >
              <Icon size={24} className={stage.color} />
            </motion.div>
          </AnimatePresence>
        </motion.div>

        {/* Stage text */}
        <AnimatePresence mode="wait">
          <motion.div
            key={stageIndex}
            initial={{ opacity: 0, y: 5 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -5 }}
            transition={{ duration: 0.3 }}
          >
            <p className="text-base font-semibold">{stage.label}</p>
            <p className="text-xs text-massclaw-text-muted mt-0.5">{stage.sublabel}</p>
          </motion.div>
        </AnimatePresence>

        {/* Stage dots */}
        <div className="flex items-center gap-1.5">
          {THINKING_STAGES.map((_, i) => (
            <motion.div
              key={i}
              className="rounded-full"
              animate={{
                width: i === stageIndex ? 24 : 8,
                height: 4,
                backgroundColor: i <= stageIndex ? "rgb(99, 102, 241)" : "rgba(255,255,255,0.08)",
              }}
              transition={{ duration: 0.4, ease: [0.2, 0.65, 0.3, 0.9] }}
            />
          ))}
        </div>

        {/* Live status message */}
        <div className="text-[11px] text-massclaw-text-muted space-y-0.5">
          {totalCount > 0 ? (
            <p>{completedCount}/{totalCount} tasks completed</p>
          ) : (
            <p>Preparing execution plan...</p>
          )}
          <ElapsedTimer />
        </div>

      </div>
    </motion.div>
  );
}

/* Elapsed time counter */
function ElapsedTimer() {
  const [seconds, setSeconds] = React.useState(0);
  React.useEffect(() => {
    const interval = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(interval);
  }, []);
  return (
    <motion.p
      className="text-massclaw-text-muted/50 font-mono text-[10px]"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
    >
      {seconds}s elapsed
    </motion.p>
  );
}
