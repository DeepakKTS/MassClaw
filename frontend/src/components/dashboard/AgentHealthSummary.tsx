"use client";
import { useAgents } from "@/hooks/useAgents";
import { getStatusColor } from "@/lib/utils";

export function AgentHealthSummary() {
  const { data, isLoading } = useAgents(1, 100);

  const counts = { active: 0, degraded: 0, suspended: 0, inactive: 0 };
  data?.items?.forEach((a) => { counts[a.status as keyof typeof counts] = (counts[a.status as keyof typeof counts] || 0) + 1; });

  return (
    <div className="bg-massclaw-surface border border-massclaw-border rounded-lg p-5">
      <h2 className="text-sm font-semibold mb-4">Agent Health</h2>
      {isLoading ? (
        <p className="text-massclaw-text-muted text-sm">Loading...</p>
      ) : (
        <div className="space-y-3">
          {Object.entries(counts).map(([status, count]) => (
            <div key={status} className="flex items-center justify-between">
              <span className={`text-sm capitalize ${getStatusColor(status)}`}>{status}</span>
              <span className="text-sm font-medium">{count}</span>
            </div>
          ))}
          <div className="pt-2 border-t border-massclaw-border flex justify-between">
            <span className="text-sm text-massclaw-text-muted">Total</span>
            <span className="text-sm font-medium">{data?.total ?? 0}</span>
          </div>
        </div>
      )}
    </div>
  );
}
