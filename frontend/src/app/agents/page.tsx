"use client";

import { useAgents } from "@/hooks/useAgents";
import { getStatusColor } from "@/lib/utils";
import Link from "next/link";
import { Bot, Search } from "lucide-react";
import { useState } from "react";

export default function AgentsPage() {
  const [page, setPage] = useState(1);
  const { data, isLoading } = useAgents(page);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Agent Registry</h1>
          <p className="text-massclaw-text-muted mt-1">
            {data?.total ?? 0} agents registered
          </p>
        </div>
      </div>

      {isLoading ? (
        <div className="text-massclaw-text-muted">Loading agents...</div>
      ) : (
        <div className="grid gap-4">
          {data?.items.map((agent) => (
            <Link
              key={agent.agent_id}
              href={`/agents/${agent.agent_id}`}
              className="bg-massclaw-surface border border-massclaw-border rounded-lg p-5 hover:border-massclaw-accent/40 transition"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <Bot size={20} className="text-massclaw-accent" />
                  <div>
                    <h3 className="font-medium">{agent.name}</h3>
                    <p className="text-xs text-massclaw-text-muted mt-0.5">
                      v{agent.version} &middot; Safety Level {agent.safety_level}
                    </p>
                  </div>
                </div>
                <div className="text-right">
                  <span className={`text-sm font-medium ${getStatusColor(agent.status)}`}>
                    {agent.status}
                  </span>
                  <p className="text-xs text-massclaw-text-muted mt-0.5">
                    Trust: {(agent.trust_score * 100).toFixed(1)}%
                  </p>
                </div>
              </div>
              <div className="flex gap-2 mt-3">
                {agent.capabilities.map((cap: string) => (
                  <span
                    key={cap}
                    className="text-xs px-2 py-0.5 rounded bg-massclaw-border text-massclaw-text-muted"
                  >
                    {cap}
                  </span>
                ))}
              </div>
            </Link>
          ))}
        </div>
      )}

      {data && data.total > 20 && (
        <div className="flex justify-center gap-2">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
            className="px-3 py-1 text-sm bg-massclaw-surface border border-massclaw-border rounded disabled:opacity-30"
          >
            Previous
          </button>
          <span className="px-3 py-1 text-sm text-massclaw-text-muted">
            Page {page}
          </span>
          <button
            onClick={() => setPage((p) => p + 1)}
            disabled={(data?.items?.length ?? 0) < 20}
            className="px-3 py-1 text-sm bg-massclaw-surface border border-massclaw-border rounded disabled:opacity-30"
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
