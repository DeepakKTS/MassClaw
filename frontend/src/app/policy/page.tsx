"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { ShieldCheck, ShieldAlert, ShieldOff } from "lucide-react";

export default function PolicyPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["policy-rules"],
    queryFn: () => api.get<any>("/policy/rules"),
  });

  const rules = data?.items || data || [];

  const actionIcon = (action: string) => {
    if (action === "deny") return <ShieldOff size={16} className="text-red-400" />;
    if (action === "flag") return <ShieldAlert size={16} className="text-amber-400" />;
    return <ShieldCheck size={16} className="text-emerald-400" />;
  };

  return (
    <div className="max-w-4xl mx-auto space-y-6 py-6">
      <div>
        <h1 className="text-heading">Policy Rules</h1>
        <p className="text-massclaw-text-muted mt-1 text-sm">Safety and governance rule management</p>
      </div>

      <div className="space-y-3">
        {isLoading ? (
          <div className="text-massclaw-text-muted text-sm">Loading...</div>
        ) : Array.isArray(rules) && rules.length > 0 ? (
          rules.map((rule: any) => (
            <div key={rule.rule_id} className="glass rounded-xl p-4 hover:bg-white/[0.03] transition-all">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  {actionIcon(rule.action)}
                  <div>
                    <h3 className="font-medium text-sm">{rule.name}</h3>
                    <p className="text-xs text-massclaw-text-muted mt-0.5">{rule.description}</p>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <span className={`text-[10px] px-2 py-0.5 rounded-full uppercase tracking-wider font-semibold ${
                    rule.action === "deny" ? "bg-red-500/10 text-red-400" :
                    rule.action === "flag" ? "bg-amber-500/10 text-amber-400" :
                    "bg-emerald-500/10 text-emerald-400"
                  }`}>
                    {rule.action}
                  </span>
                  <div className={`w-2 h-2 rounded-full ${rule.enabled ? "bg-emerald-400" : "bg-massclaw-text-muted/30"}`} />
                </div>
              </div>
            </div>
          ))
        ) : (
          <p className="text-massclaw-text-muted text-center py-12 text-sm">No policy rules configured</p>
        )}
      </div>
    </div>
  );
}
