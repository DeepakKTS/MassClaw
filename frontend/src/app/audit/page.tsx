"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { FileText } from "lucide-react";

export default function AuditPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["audit-search"],
    queryFn: () => api.get<any>("/audit/search?page_size=50"),
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Audit Trail</h1>
        <p className="text-massclaw-text-muted mt-1">Complete decision log across the system</p>
      </div>

      <div className="bg-massclaw-surface border border-massclaw-border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-massclaw-border">
              <th className="text-left p-3 text-massclaw-text-muted font-medium">Event</th>
              <th className="text-left p-3 text-massclaw-text-muted font-medium">Actor</th>
              <th className="text-left p-3 text-massclaw-text-muted font-medium">Summary</th>
              <th className="text-left p-3 text-massclaw-text-muted font-medium">Time</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr><td colSpan={4} className="p-4 text-massclaw-text-muted">Loading...</td></tr>
            ) : data?.items?.map((log: any) => (
              <tr key={log.log_id} className="border-b border-massclaw-border/50 hover:bg-massclaw-border/20">
                <td className="p-3">
                  <span className="text-xs px-2 py-0.5 rounded bg-massclaw-border">{log.event_type}</span>
                </td>
                <td className="p-3 text-massclaw-text-muted">{log.actor_id}</td>
                <td className="p-3 max-w-md truncate">{log.input_summary || log.output_summary || "—"}</td>
                <td className="p-3 text-massclaw-text-muted text-xs">{new Date(log.created_at).toLocaleString()}</td>
              </tr>
            ))}
            {!isLoading && (!data?.items?.length) && (
              <tr><td colSpan={4} className="p-8 text-center text-massclaw-text-muted">No audit events yet</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
