"use client";

import { useAgents } from "@/hooks/useAgents";
import { getStatusColor } from "@/lib/utils";
import { TrustRing } from "@/components/effects/TrustRing";
import Link from "next/link";
import { Bot } from "lucide-react";
import { useState } from "react";

export default function AgentsPage() {
  const [page, setPage] = useState(1);
  const { data, isLoading } = useAgents(page);

  return (
    <div className="max-w-4xl mx-auto space-y-6 py-6">
      <div>
        <h1 className="text-heading">Agent Registry</h1>
        <p className="text-massclaw-text-muted mt-1 text-sm">
          {data?.total ?? 0} agents registered
        </p>
      </div>

      {isLoading ? (
        <div className="text-massclaw-text-muted text-sm">Loading agents...</div>
      ) : (
        <div className="grid gap-3">
          {data?.items.map((agent) => (
            <Link
              key={agent.agent_id}
              href={`/agents/${agent.agent_id}`}
              className="glass rounded-xl p-5 hover:bg-white/[0.04] transition-all group"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <Bot size={20} className="text-massclaw-accent" />
                  <div>
                    <h3 className="font-medium group-hover:text-white transition-colors">{agent.name}</h3>
                    <p className="text-xs text-massclaw-text-muted mt-0.5">
                      v{agent.version} &middot; Safety Level {agent.safety_level}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-4">
                  <TrustRing score={agent.trust_score} size={40} />
                  <span className={`text-xs font-medium ${getStatusColor(agent.status)}`}>
                    {agent.status}
                  </span>
                </div>
              </div>
              <div className="flex gap-2 mt-3">
                {agent.capabilities.map((cap: string) => (
                  <span
                    key={cap}
                    className="text-[10px] px-2 py-0.5 rounded-full bg-massclaw-accent/10 text-massclaw-accent"
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
            className="px-3 py-1 text-sm glass rounded-lg disabled:opacity-30"
          >
            Previous
          </button>
          <span className="px-3 py-1 text-sm text-massclaw-text-muted">Page {page}</span>
          <button
            onClick={() => setPage((p) => p + 1)}
            disabled={(data?.items?.length ?? 0) < 20}
            className="px-3 py-1 text-sm glass rounded-lg disabled:opacity-30"
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
