"use client";

import { useWorkflows } from "@/hooks/useWorkflows";
import { truncate, formatCredits } from "@/lib/utils";
import { cn } from "@/lib/utils";
import Link from "next/link";
import { motion } from "framer-motion";
import { Crosshair, Plus, Loader2, Inbox, Coins } from "lucide-react";

export default function MissionsPage() {
  const { data, isLoading } = useWorkflows();

  return (
    <div className="max-w-4xl mx-auto space-y-6 py-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-heading">Mission Log</h1>
          <p className="text-massclaw-text-muted mt-1 text-sm">
            {data?.total ?? 0} missions executed
          </p>
        </div>
        <Link
          href="/"
          className="flex items-center gap-2 px-4 py-2 bg-massclaw-accent hover:bg-massclaw-accent-light text-white rounded-xl hover:shadow-lg hover:shadow-massclaw-accent/20 transition text-sm font-medium"
        >
          <Plus size={16} />
          New Mission
        </Link>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 size={24} className="animate-spin text-massclaw-accent" />
        </div>
      ) : !data?.items?.length ? (
        <div className="flex flex-col items-center justify-center py-20 text-massclaw-text-muted">
          <Inbox size={40} className="mb-4 opacity-30" />
          <p className="text-sm">No missions yet</p>
          <p className="text-xs mt-1 opacity-60">Launch your first mission from Mission Control</p>
        </div>
      ) : (
        <div className="space-y-2">
          {data.items.map((wf, i) => {
            const isActive = ["pending", "decomposing", "running"].includes(wf.status);
            const budgetPercent = wf.budget_limit ? (wf.budget_used / wf.budget_limit) * 100 : 0;

            return (
              <motion.div
                key={wf.workflow_id}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.03 }}
              >
                <Link href={`/missions/${wf.workflow_id}`}>
                  <div className={cn(
                    "group glass rounded-xl p-5 hover:bg-white/[0.04] transition-all cursor-pointer relative overflow-hidden",
                    isActive && "border-massclaw-accent/20"
                  )}>
                    {isActive && (
                      <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-massclaw-accent to-transparent" />
                    )}

                    <div className="flex items-start justify-between gap-4">
                      <div className="flex items-start gap-3 flex-1 min-w-0">
                        <Crosshair size={18} className="text-massclaw-accent mt-0.5 shrink-0" />
                        <div className="min-w-0">
                          <p className="font-medium text-sm group-hover:text-white transition-colors">
                            {truncate(wf.prompt, 100)}
                          </p>
                          <div className="flex items-center gap-3 mt-1.5 text-[11px] text-massclaw-text-muted">
                            {wf.domain && (
                              <span className="px-1.5 py-0.5 rounded bg-massclaw-accent/10 text-massclaw-accent text-[10px]">
                                {wf.domain}
                              </span>
                            )}
                            <span className="flex items-center gap-1">
                              <Coins size={10} />
                              {formatCredits(wf.budget_used)} / {formatCredits(wf.budget_limit)}
                            </span>
                          </div>
                        </div>
                      </div>

                      <span className={cn(
                        "text-[10px] font-semibold px-2.5 py-1 rounded-full uppercase tracking-wider whitespace-nowrap",
                        wf.status === "completed" && "bg-emerald-500/15 text-emerald-400",
                        wf.status === "failed" && "bg-red-500/15 text-red-400",
                        isActive && "bg-indigo-500/15 text-indigo-400 animate-pulse",
                        wf.status === "cancelled" && "bg-gray-500/15 text-gray-400"
                      )}>
                        {wf.status}
                      </span>
                    </div>

                    {/* Budget bar */}
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
          })}
        </div>
      )}
    </div>
  );
}
