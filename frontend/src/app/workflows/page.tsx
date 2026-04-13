"use client";

import { useWorkflows } from "@/hooks/useWorkflows";
import { getStatusColor, truncate, formatCredits } from "@/lib/utils";
import Link from "next/link";
import { GitBranch, Plus } from "lucide-react";

export default function WorkflowsPage() {
  const { data, isLoading } = useWorkflows();

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Workflows</h1>
          <p className="text-massclaw-text-muted mt-1">
            {data?.total ?? 0} workflows
          </p>
        </div>
        <Link
          href="/workflows/new"
          className="flex items-center gap-2 px-4 py-2 bg-massclaw-accent text-white rounded-lg hover:bg-massclaw-accent-light transition text-sm"
        >
          <Plus size={16} />
          New Workflow
        </Link>
      </div>

      {isLoading ? (
        <div className="text-massclaw-text-muted">Loading...</div>
      ) : (
        <div className="space-y-3">
          {data?.items.map((wf) => (
            <Link
              key={wf.workflow_id}
              href={`/workflows/${wf.workflow_id}`}
              className="block bg-massclaw-surface border border-massclaw-border rounded-lg p-5 hover:border-massclaw-accent/40 transition"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <GitBranch size={18} className="text-massclaw-accent" />
                  <div>
                    <p className="font-medium">{truncate(wf.prompt, 80)}</p>
                    <p className="text-xs text-massclaw-text-muted mt-0.5">
                      {wf.domain ?? "general"} &middot; {formatCredits(wf.budget_used)} / {formatCredits(wf.budget_limit)}
                    </p>
                  </div>
                </div>
                <span className={`text-sm font-medium ${getStatusColor(wf.status)}`}>
                  {wf.status}
                </span>
              </div>
            </Link>
          ))}
          {!data?.items?.length && (
            <p className="text-massclaw-text-muted text-center py-8">
              No workflows yet. Create one to get started.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
