"use client";
import { useWorkflows } from "@/hooks/useWorkflows";
import { getStatusColor, truncate } from "@/lib/utils";
import Link from "next/link";

export function RecentWorkflows() {
  const { data, isLoading } = useWorkflows();

  return (
    <div className="bg-massclaw-surface border border-massclaw-border rounded-lg p-5">
      <h2 className="text-sm font-semibold mb-4">Recent Missions</h2>
      {isLoading ? (
        <p className="text-massclaw-text-muted text-sm">Loading...</p>
      ) : !data?.items?.length ? (
        <p className="text-massclaw-text-muted text-sm">No missions yet</p>
      ) : (
        <div className="space-y-3">
          {data.items.slice(0, 5).map((wf) => (
            <Link key={wf.workflow_id} href={`/missions/${wf.workflow_id}`} className="block hover:bg-massclaw-border/20 rounded p-2 -mx-2 transition">
              <div className="flex items-center justify-between">
                <span className="text-sm">{truncate(wf.prompt, 50)}</span>
                <span className={`text-xs font-medium ${getStatusColor(wf.status)}`}>{wf.status}</span>
              </div>
              <div className="text-xs text-massclaw-text-muted mt-1">
                {wf.domain ?? "general"} &middot; {wf.budget_used.toFixed(1)}/{wf.budget_limit.toFixed(0)} credits
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
