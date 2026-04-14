"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export default function AuditPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["audit-search"],
    queryFn: () => api.get<any>("/audit/search?page_size=50"),
  });

  return (
    <div className="max-w-5xl mx-auto space-y-6 py-6">
      <div>
        <h1 className="text-heading">Audit Trail</h1>
        <p className="text-massclaw-text-muted mt-1 text-sm">Complete decision log across the system</p>
      </div>

      <div className="glass rounded-xl overflow-hidden font-mono">
        <div className="p-4 border-b border-white/[0.06] flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-emerald-400" />
          <span className="text-[10px] text-emerald-400 uppercase tracking-wider">System Log</span>
        </div>
        <div className="divide-y divide-white/[0.04]">
          {isLoading ? (
            <div className="p-4 text-massclaw-text-muted text-sm">Loading...</div>
          ) : data?.items?.map((log: any) => (
            <div key={log.log_id} className="px-4 py-3 hover:bg-white/[0.02] transition-colors text-xs">
              <div className="flex items-center gap-3">
                <span className="text-massclaw-text-muted whitespace-nowrap">
                  {new Date(log.created_at).toLocaleString()}
                </span>
                <span className="px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400 text-[10px]">
                  {log.event_type}
                </span>
                <span className="text-massclaw-text-muted">{log.actor_id}</span>
                <span className="text-massclaw-text truncate flex-1">
                  {log.input_summary || log.output_summary || "—"}
                </span>
              </div>
            </div>
          ))}
          {!isLoading && (!data?.items?.length) && (
            <div className="p-8 text-center text-massclaw-text-muted text-sm">No audit events yet</div>
          )}
        </div>
      </div>
    </div>
  );
}
