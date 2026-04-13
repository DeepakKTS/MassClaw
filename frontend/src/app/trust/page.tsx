"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Shield } from "lucide-react";

export default function TrustPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["trust-leaderboard"],
    queryFn: () => api.get<any[]>("/trust/leaderboard/ranked?limit=20"),
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Trust Leaderboard</h1>
        <p className="text-massclaw-text-muted mt-1">Agents ranked by Bayesian trust score</p>
      </div>

      <div className="bg-massclaw-surface border border-massclaw-border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-massclaw-border">
              <th className="text-left p-4 text-massclaw-text-muted font-medium">Rank</th>
              <th className="text-left p-4 text-massclaw-text-muted font-medium">Agent</th>
              <th className="text-right p-4 text-massclaw-text-muted font-medium">Trust Score</th>
              <th className="text-right p-4 text-massclaw-text-muted font-medium">Interactions</th>
              <th className="text-right p-4 text-massclaw-text-muted font-medium">Trend</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr><td colSpan={5} className="p-4 text-massclaw-text-muted">Loading...</td></tr>
            ) : data?.map((agent: any) => (
              <tr key={agent.agent_id} className="border-b border-massclaw-border/50 hover:bg-massclaw-border/20">
                <td className="p-4 font-medium">#{agent.rank}</td>
                <td className="p-4 flex items-center gap-2">
                  <Shield size={14} className="text-massclaw-accent" />
                  {agent.agent_name}
                </td>
                <td className="p-4 text-right font-mono">{(agent.trust_score * 100).toFixed(1)}%</td>
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
