"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Trophy, Crown } from "lucide-react";
import { cn } from "@/lib/utils";

const RANK_STYLES: Record<number, string> = {
  1: "text-amber-400",
  2: "text-gray-300",
  3: "text-amber-600",
};

export default function EvolutionPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["evolution-rankings"],
    queryFn: () => api.get<any[]>("/evolution/rankings/leaderboard?limit=20"),
  });

  return (
    <div className="max-w-4xl mx-auto space-y-6 py-6">
      <div>
        <h1 className="text-heading">Evolution Rankings</h1>
        <p className="text-massclaw-text-muted mt-1 text-sm">Agent performance rankings by composite score</p>
      </div>

      <div className="glass rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-white/[0.06]">
              <th className="text-left p-4 text-massclaw-text-muted font-medium text-xs uppercase tracking-wider">Rank</th>
              <th className="text-left p-4 text-massclaw-text-muted font-medium text-xs uppercase tracking-wider">Agent</th>
              <th className="text-right p-4 text-massclaw-text-muted font-medium text-xs uppercase tracking-wider">Composite</th>
              <th className="text-right p-4 text-massclaw-text-muted font-medium text-xs uppercase tracking-wider">Tasks</th>
              <th className="text-right p-4 text-massclaw-text-muted font-medium text-xs uppercase tracking-wider">Delta</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr><td colSpan={5} className="p-4 text-massclaw-text-muted">Loading...</td></tr>
            ) : data?.map((a: any) => (
              <tr key={a.agent_id} className="border-b border-white/[0.04] hover:bg-white/[0.02] transition-colors">
                <td className="p-4">
                  <span className={cn("font-bold", RANK_STYLES[a.rank])}>
                    {a.rank <= 3 && <Crown size={12} className="inline mr-1" />}
                    #{a.rank}
                  </span>
                </td>
                <td className="p-4 flex items-center gap-2">
                  <Trophy size={14} className="text-amber-400" />
                  {a.agent_name}
                </td>
                <td className="p-4 text-right font-mono text-massclaw-accent">{(a.composite_score * 100).toFixed(1)}%</td>
                <td className="p-4 text-right text-massclaw-text-muted">{a.total_tasks}</td>
                <td className="p-4 text-right">
                  <span className={a.delta_from_previous >= 0 ? "text-massclaw-success" : "text-massclaw-danger"}>
                    {a.delta_from_previous >= 0 ? "+" : ""}{(a.delta_from_previous * 100).toFixed(2)}%
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
