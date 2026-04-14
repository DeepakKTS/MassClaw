"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Shield, Crown } from "lucide-react";
import { cn } from "@/lib/utils";

const RANK_STYLES: Record<number, string> = {
  1: "text-amber-400",
  2: "text-gray-300",
  3: "text-amber-600",
};

export default function TrustPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["trust-leaderboard"],
    queryFn: () => api.get<any[]>("/trust/leaderboard/ranked?limit=20"),
  });

  return (
    <div className="max-w-4xl mx-auto space-y-6 py-6">
      <div>
        <h1 className="text-heading">Trust Leaderboard</h1>
        <p className="text-massclaw-text-muted mt-1 text-sm">Agents ranked by Bayesian trust score</p>
      </div>

      <div className="glass rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-white/[0.06]">
              <th className="text-left p-4 text-massclaw-text-muted font-medium text-xs uppercase tracking-wider">Rank</th>
              <th className="text-left p-4 text-massclaw-text-muted font-medium text-xs uppercase tracking-wider">Agent</th>
              <th className="text-right p-4 text-massclaw-text-muted font-medium text-xs uppercase tracking-wider">Trust Score</th>
              <th className="text-right p-4 text-massclaw-text-muted font-medium text-xs uppercase tracking-wider">Interactions</th>
              <th className="text-right p-4 text-massclaw-text-muted font-medium text-xs uppercase tracking-wider">Trend</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr><td colSpan={5} className="p-4 text-massclaw-text-muted">Loading...</td></tr>
            ) : data?.map((agent: any) => (
              <tr key={agent.agent_id} className="border-b border-white/[0.04] hover:bg-white/[0.02] transition-colors">
                <td className="p-4">
                  <span className={cn("font-bold", RANK_STYLES[agent.rank])}>
                    {agent.rank <= 3 && <Crown size={12} className="inline mr-1" />}
                    #{agent.rank}
                  </span>
                </td>
                <td className="p-4 flex items-center gap-2">
                  <Shield size={14} className="text-massclaw-accent" />
                  {agent.agent_name}
                </td>
                <td className="p-4 text-right font-mono text-massclaw-accent">{(agent.trust_score * 100).toFixed(1)}%</td>
                <td className="p-4 text-right text-massclaw-text-muted">{agent.total_interactions}</td>
                <td className="p-4 text-right">
                  <span className={agent.trend >= 0 ? "text-massclaw-success" : "text-massclaw-danger"}>
                    {agent.trend >= 0 ? "+" : ""}{(agent.trend * 100).toFixed(2)}%
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
