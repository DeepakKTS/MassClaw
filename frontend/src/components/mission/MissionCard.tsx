"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { Crosshair, Clock, Coins } from "lucide-react";
import { cn, truncate, getStatusColor } from "@/lib/utils";
import type { Workflow } from "@/types/workflow";

interface MissionCardProps {
  mission: Workflow;
  index: number;
}

export function MissionCard({ mission, index }: MissionCardProps) {
  const isActive = ["pending", "decomposing", "running"].includes(
    mission.status
  );
  const budgetPercent = mission.budget_limit
    ? (mission.budget_used / mission.budget_limit) * 100
    : 0;

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.05 }}
    >
      <Link href={`/missions/${mission.workflow_id}`}>
        <div
          className={cn(
            "group glass rounded-xl p-4 hover:bg-white/[0.04] transition-all cursor-pointer relative overflow-hidden",
            isActive && "border-massclaw-accent/20"
          )}
        >
          {/* Status glow for active */}
          {isActive && (
            <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-massclaw-accent to-transparent" />
          )}

          <div className="flex items-start justify-between gap-3">
            <div className="flex-1 min-w-0">
              {/* Prompt */}
              <p className="text-sm text-massclaw-text group-hover:text-white transition-colors">
                {truncate(mission.prompt, 80)}
              </p>

              {/* Meta row */}
              <div className="flex items-center gap-3 mt-2 text-[11px] text-massclaw-text-muted">
                {mission.domain && (
                  <span className="px-1.5 py-0.5 rounded bg-massclaw-accent/10 text-massclaw-accent text-[10px]">
                    {mission.domain}
                  </span>
                )}
                <span className="flex items-center gap-1">
                  <Coins size={10} />
                  {mission.budget_used.toFixed(0)}/{mission.budget_limit} cr
                </span>
              </div>
            </div>

            {/* Status badge */}
            <span
              className={cn(
                "text-[10px] font-semibold px-2 py-0.5 rounded-full uppercase tracking-wider whitespace-nowrap",
                mission.status === "completed" &&
                  "bg-emerald-500/15 text-emerald-400",
                mission.status === "failed" && "bg-red-500/15 text-red-400",
                isActive && "bg-indigo-500/15 text-indigo-400 animate-pulse",
                mission.status === "cancelled" &&
                  "bg-gray-500/15 text-gray-400"
              )}
            >
              {mission.status}
            </span>
          </div>

          {/* Budget progress bar */}
          <div className="mt-3 h-0.5 rounded-full bg-white/[0.04] overflow-hidden">
            <div
              className="h-full rounded-full bg-massclaw-accent transition-all duration-500"
              style={{ width: `${Math.min(budgetPercent, 100)}%` }}
            />
          </div>
        </div>
      </Link>
    </motion.div>
  );
}
