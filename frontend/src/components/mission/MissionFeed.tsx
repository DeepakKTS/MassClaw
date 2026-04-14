"use client";

import { useWorkflows } from "@/hooks/useWorkflows";
import { MissionCard } from "./MissionCard";
import { Loader2, Inbox } from "lucide-react";
import Link from "next/link";

export function MissionFeed() {
  const { data, isLoading } = useWorkflows();

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 size={20} className="animate-spin text-massclaw-accent" />
      </div>
    );
  }

  const missions = data?.items ?? [];

  if (missions.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-massclaw-text-muted">
        <Inbox size={32} className="mb-3 opacity-40" />
        <p className="text-sm">No missions yet</p>
        <p className="text-xs mt-1 opacity-60">
          Launch your first mission above
        </p>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-xs font-medium text-massclaw-text-muted uppercase tracking-wider">
          Mission Log
        </h2>
        {missions.length > 5 && (
          <Link
            href="/missions"
            className="text-xs text-massclaw-accent hover:text-massclaw-accent-light transition-colors"
          >
            View all
          </Link>
        )}
      </div>
      <div className="space-y-2">
        {missions.slice(0, 8).map((mission, i) => (
          <MissionCard
            key={mission.workflow_id}
            mission={mission}
            index={i}
          />
        ))}
      </div>
    </div>
  );
}
