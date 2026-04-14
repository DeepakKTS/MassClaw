"use client";

import { useWorkflows, useWorkflowStatus } from "@/hooks/useWorkflows";
import Link from "next/link";
import { ExternalLink, Loader2 } from "lucide-react";
import { motion } from "framer-motion";

export function ActiveMission() {
  const { data } = useWorkflows();
  const missions = data?.items ?? [];

  const activeMission = missions.find((m) =>
    ["pending", "decomposing", "running"].includes(m.status)
  );

  if (!activeMission) return null;

  return <ActiveMissionInner id={activeMission.workflow_id} prompt={activeMission.prompt} />;
}

function ActiveMissionInner({ id, prompt }: { id: string; prompt: string }) {
  const { data: status } = useWorkflowStatus(id);

  const progressPercent = status?.progress_percent || 0;
  const completedTasks = status?.completed_tasks || 0;
  const totalTasks = status?.total_tasks || 0;

  return (
    <Link href={`/missions/${id}`}>
      <motion.div
        className="glass rounded-xl p-4 hover:bg-white/[0.04] transition-all cursor-pointer group relative overflow-hidden"
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
      >
        {/* Active glow line */}
        <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-massclaw-accent to-transparent" />

        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <Loader2 size={12} className="text-massclaw-accent animate-spin" />
            <span className="text-[10px] font-medium text-massclaw-accent uppercase tracking-wider">
              Active Mission
            </span>
          </div>
          <div className="flex items-center gap-1 text-[10px] text-massclaw-text-muted group-hover:text-massclaw-accent transition-colors">
            View <ExternalLink size={9} />
          </div>
        </div>

        <p className="text-sm text-massclaw-text line-clamp-1 mb-2">{prompt}</p>

        {/* Compact progress */}
        <div className="flex items-center gap-3">
          <div className="flex-1 h-1 rounded-full bg-white/[0.04] overflow-hidden">
            <motion.div
              className="h-full rounded-full bg-massclaw-accent"
              animate={{ width: `${progressPercent}%` }}
              transition={{ duration: 0.5 }}
            />
          </div>
          <span className="text-[10px] font-mono text-massclaw-text-muted">
            {totalTasks > 0 ? `${completedTasks}/${totalTasks} ops` : "preparing..."}
          </span>
        </div>
      </motion.div>
    </Link>
  );
}
