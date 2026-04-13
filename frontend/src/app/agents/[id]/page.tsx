"use client";

import { useParams } from "next/navigation";
import { useAgent } from "@/hooks/useAgents";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { getStatusColor } from "@/lib/utils";
import { Bot, Shield, Zap, DollarSign, Clock } from "lucide-react";

export default function AgentDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data: agent, isLoading } = useAgent(id);
  const { data: trustBreakdown } = useQuery({
    queryKey: ["trust-breakdown", id],
    queryFn: () => api.get<any>(`/trust/${id}`),
    enabled: !!id,
  });
  const { data: evolution } = useQuery({
    queryKey: ["evolution", id],
    queryFn: () => api.get<any>(`/evolution/${id}`),
    enabled: !!id,
  });

  if (isLoading || !agent) return <div className="text-massclaw-text-muted">Loading agent...</div>;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4">
        <div className="p-3 rounded-lg bg-massclaw-accent/10">
          <Bot size={28} className="text-massclaw-accent" />
        </div>
        <div>
          <h1 className="text-2xl font-bold">{agent.name}</h1>
          <p className="text-massclaw-text-muted text-sm">v{agent.version} · Safety Level {agent.safety_level}</p>
        </div>
        <span className={`ml-auto text-sm font-medium px-3 py-1 rounded-full border ${getStatusColor(agent.status)} border-current/20`}>
          {agent.status}
        </span>
      </div>

      <p className="text-sm text-massclaw-text-muted">{agent.description}</p>

      <div className="flex flex-wrap gap-2">
        {agent.capabilities.map((cap: string) => (
          <span key={cap} className="px-3 py-1 text-xs rounded-full bg-massclaw-accent/10 text-massclaw-accent border border-massclaw-accent/20">{cap}</span>
        ))}
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-massclaw-surface border border-massclaw-border rounded-lg p-4">
          <div className="flex items-center gap-2 mb-1"><Shield size={14} className="text-massclaw-accent" /><span className="text-xs text-massclaw-text-muted">Trust Score</span></div>
          <p className="text-xl font-bold">{(agent.trust_score * 100).toFixed(1)}%</p>
        </div>
        <div className="bg-massclaw-surface border border-massclaw-border rounded-lg p-4">
          <div className="flex items-center gap-2 mb-1"><DollarSign size={14} className="text-amber-400" /><span className="text-xs text-massclaw-text-muted">Avg Cost</span></div>
          <p className="text-xl font-bold">${agent.cost_profile?.avg_cost_per_call?.toFixed(3) ?? "—"}</p>
        </div>
        <div className="bg-massclaw-surface border border-massclaw-border rounded-lg p-4">
          <div className="flex items-center gap-2 mb-1"><Clock size={14} className="text-blue-400" /><span className="text-xs text-massclaw-text-muted">P95 Latency</span></div>
          <p className="text-xl font-bold">{agent.latency_profile?.p95_ms ? `${(agent.latency_profile.p95_ms / 1000).toFixed(1)}s` : "—"}</p>
        </div>
        <div className="bg-massclaw-surface border border-massclaw-border rounded-lg p-4">
          <div className="flex items-center gap-2 mb-1"><Zap size={14} className="text-emerald-400" /><span className="text-xs text-massclaw-text-muted">Interactions</span></div>
          <p className="text-xl font-bold">{trustBreakdown?.total_interactions ?? 0}</p>
        </div>
      </div>

      {/* Trust Breakdown */}
      {trustBreakdown && trustBreakdown.total_interactions > 0 && (
        <div className="bg-massclaw-surface border border-massclaw-border rounded-lg p-5">
          <h2 className="text-sm font-semibold mb-4">Trust Breakdown</h2>
          <div className="grid grid-cols-5 gap-4">
            {[
              { label: "Quality", value: trustBreakdown.quality_avg },
              { label: "Speed", value: trustBreakdown.speed_avg },
              { label: "Cost", value: trustBreakdown.cost_avg },
              { label: "Consistency", value: trustBreakdown.consistency_avg },
              { label: "Reliability", value: trustBreakdown.reliability_avg },
            ].map(({ label, value }) => (
              <div key={label} className="text-center">
                <div className="h-20 flex items-end justify-center mb-2">
                  <div className="w-8 bg-massclaw-accent/20 rounded-t" style={{ height: `${value * 100}%` }}>
                    <div className="w-full bg-massclaw-accent rounded-t" style={{ height: `${value * 100}%` }} />
                  </div>
                </div>
                <p className="text-xs text-massclaw-text-muted">{label}</p>
                <p className="text-sm font-medium">{(value * 100).toFixed(0)}%</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Evolution */}
      {evolution && evolution.total_scored_tasks > 0 && (
        <div className="bg-massclaw-surface border border-massclaw-border rounded-lg p-5">
          <h2 className="text-sm font-semibold mb-3">Evolution</h2>
          <div className="grid grid-cols-3 gap-4 text-sm">
            <div>
              <p className="text-massclaw-text-muted text-xs">Composite</p>
              <p className="font-medium">{(evolution.current_composite * 100).toFixed(1)}%</p>
            </div>
            <div>
              <p className="text-massclaw-text-muted text-xs">Percentile</p>
              <p className="font-medium">{evolution.percentile.toFixed(0)}th</p>
            </div>
            <div>
              <p className="text-massclaw-text-muted text-xs">7d Trend</p>
              <p className={`font-medium ${evolution.trend_7d >= 0 ? "text-massclaw-success" : "text-massclaw-danger"}`}>
                {evolution.trend_7d >= 0 ? "+" : ""}{(evolution.trend_7d * 100).toFixed(2)}%
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
