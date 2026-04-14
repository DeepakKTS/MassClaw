"use client";

import { useAgents } from "@/hooks/useAgents";
import { useWorkflows } from "@/hooks/useWorkflows";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Bot, Crosshair, Brain, Shield } from "lucide-react";

function Stat({
  icon: Icon,
  value,
  label,
  color,
}: {
  icon: any;
  value: string | number;
  label: string;
  color: string;
}) {
  return (
    <div className="flex items-center gap-2.5 px-4 py-2">
      <div className={`p-1.5 rounded-lg ${color}`}>
        <Icon size={14} />
      </div>
      <div>
        <p className="text-sm font-semibold leading-none">{value}</p>
        <p className="text-[10px] text-massclaw-text-muted leading-none mt-0.5">
          {label}
        </p>
      </div>
    </div>
  );
}

export function QuickStats() {
  const { data: agents } = useAgents(1, 100);
  const { data: workflows } = useWorkflows();
  const { data: metrics } = useQuery({
    queryKey: ["system-metrics"],
    queryFn: () => api.get<any>("/../../system/metrics"),
    retry: false,
  });

  const activeAgents =
    agents?.items?.filter((a: any) => a.status === "active").length ?? 0;

  return (
    <div className="flex flex-wrap items-center gap-1 glass rounded-xl">
      <Stat
        icon={Bot}
        value={activeAgents}
        label="Agents"
        color="bg-indigo-500/10 text-indigo-400"
      />
      <div className="w-px h-6 bg-white/[0.06]" />
      <Stat
        icon={Crosshair}
        value={workflows?.total ?? 0}
        label="Missions"
        color="bg-emerald-500/10 text-emerald-400"
      />
      <div className="w-px h-6 bg-white/[0.06]" />
      <Stat
        icon={Brain}
        value={metrics?.memory_records ?? "—"}
        label="Memories"
        color="bg-purple-500/10 text-purple-400"
      />
      <div className="w-px h-6 bg-white/[0.06]" />
      <Stat
        icon={Shield}
        value={metrics?.trust_events ?? "—"}
        label="Trust Events"
        color="bg-amber-500/10 text-amber-400"
      />
    </div>
  );
}
