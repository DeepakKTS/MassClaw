"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Settings, Plus } from "lucide-react";

export default function PolicyPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["policy-rules"],
    queryFn: () => api.get<any>("/policy/rules"),
  });

  const rules = data?.items || data || [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Policy Rules</h1>
          <p className="text-massclaw-text-muted mt-1">Safety and governance rule management</p>
        </div>
      </div>

      <div className="space-y-3">
        {isLoading ? (
          <div className="text-massclaw-text-muted">Loading...</div>
        ) : Array.isArray(rules) && rules.length > 0 ? (
          rules.map((rule: any) => (
            <div key={rule.rule_id} className="bg-massclaw-surface border border-massclaw-border rounded-lg p-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <Settings size={16} className="text-massclaw-accent" />
                  <div>
                    <h3 className="font-medium">{rule.name}</h3>
                    <p className="text-xs text-massclaw-text-muted">{rule.description}</p>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <span className={`text-xs px-2 py-0.5 rounded ${rule.action === "deny" ? "bg-red-500/10 text-red-400" : rule.action === "flag" ? "bg-amber-500/10 text-amber-400" : "bg-green-500/10 text-green-400"}`}>
                    {rule.action}
                  </span>
                  <span className={`text-xs ${rule.enabled ? "text-massclaw-success" : "text-massclaw-text-muted"}`}>
                    {rule.enabled ? "Active" : "Disabled"}
                  </span>
                </div>
              </div>
            </div>
          ))
        ) : (
          <p className="text-massclaw-text-muted text-center py-8">No policy rules configured</p>
        )}
      </div>
    </div>
  );
}
