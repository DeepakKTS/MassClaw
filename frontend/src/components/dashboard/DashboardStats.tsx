"use client";

import { useAgents } from "@/hooks/useAgents";
import { useWorkflows } from "@/hooks/useWorkflows";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Bot, GitBranch, Brain, DollarSign, Shield, Activity } from "lucide-react";

function StatCard({ icon: Icon, label, value, subtitle, color }: {
  icon: any; label: string; value: string | number; subtitle?: string; color: string;
}) {
  return (
    <div className="bg-massclaw-surface border border-massclaw-border rounded-xl p-5 hover:border-massclaw-accent/20 transition">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-massclaw-text-muted text-xs font-medium uppercase tracking-wide">{label}</p>
          <p className="text-2xl font-bold mt-1.5">{value}</p>
          {subtitle && <p className="text-xs text-massclaw-text-muted mt-0.5">{subtitle}</p>}
        </div>
        <div className={`p-2.5 rounded-xl ${color}`}>
          <Icon size={20} />
        </div>
      </div>
    </div>
  );
}

export function DashboardStats() {
  const { data: agents } = useAgents(1, 100);
  const { data: workflows } = useWorkflows();
  const { data: metrics } = useQuery({
    queryKey: ["system-metrics"],
    queryFn: () => api.get<any>("/../../system/metrics"),
    retry: false,
  });

  const activeAgents = agents?.items?.filter((a: any) => a.status === "active").length ?? 0;
  const totalAgents = agents?.total ?? 0;

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
      <StatCard
        icon={Bot}
        label="Agents"
        value={activeAgents}
        subtitle={`${totalAgents} total registered`}
        color="bg-indigo-500/10 text-indigo-400"
      />
      <StatCard
        icon={GitBranch}
        label="Workflows"
        value={workflows?.total ?? 0}
        subtitle="total executed"
        color="bg-emerald-500/10 text-emerald-400"
      />
      <StatCard
        icon={Brain}
        label="Memories"
        value={metrics?.memory_records ?? "—"}
        subtitle="shared context records"
        color="bg-purple-500/10 text-purple-400"
      />
      <StatCard
        icon={Shield}
        label="Trust Events"
        value={metrics?.trust_events ?? "—"}
        subtitle="scoring updates"
        color="bg-amber-500/10 text-amber-400"
      />
    </div>
  );
}
