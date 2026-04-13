"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Trophy } from "lucide-react";

export default function EvolutionPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["evolution-rankings"],
    queryFn: () => api.get<any[]>("/evolution/rankings/leaderboard?limit=20"),
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Evolution Rankings</h1>
        <p className="text-massclaw-text-muted mt-1">Agent performance rankings by composite score</p>
      </div>

      <div className="bg-massclaw-surface border border-massclaw-border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-massclaw-border">
              <th className="text-left p-4 text-massclaw-text-muted font-medium">Rank</th>
              <th className="text-left p-4 text-massclaw-text-muted font-medium">Agent</th>
              <th className="text-right p-4 text-massclaw-text-muted font-medium">Composite</th>
              <th className="text-right p-4 text-massclaw-text-muted font-medium">Tasks</th>
              <th className="text-right p-4 text-massclaw-text-muted font-medium">Delta</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr><td colSpan={5} className="p-4 text-massclaw-text-muted">Loading...</td></tr>
            ) : data?.map((a: any) => (
              <tr key={a.agent_id} className="border-b border-massclaw-border/50 hover:bg-massclaw-border/20">
                <td className="p-4 font-medium">#{a.rank}</td>
                <td className="p-4 flex items-center gap-2">
                  <Trophy size={14} className="text-amber-400" />
                  {a.agent_name}
                </td>
                <td className="p-4 text-right font-mono">{(a.composite_score * 100).toFixed(1)}%</td>
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
