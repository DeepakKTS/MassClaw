"use client";

import { useEffect, useRef } from "react";

import type { TranscriptEntry } from "../types";

type Props = {
  entries: TranscriptEntry[];
};

function statusColor(status: number | null): string {
  if (status == null) return "text-massclaw-text-muted";
  if (status >= 200 && status < 300) return "text-massclaw-success";
  if (status >= 400 && status < 500) return "text-massclaw-warning";
  if (status >= 500) return "text-massclaw-danger";
  return "text-massclaw-text-muted";
}

export function AgentTranscript({ entries }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [entries.length]);
  return (
    <div className="glass rounded-lg border border-massclaw-border flex flex-col h-full overflow-hidden">
      <div className="px-4 py-3 border-b border-massclaw-border flex items-center justify-between">
        <span className="text-title font-semibold text-massclaw-text">Live transcript</span>
        <span className="text-caption text-massclaw-text-muted">subagent turns + HTTP calls</span>
      </div>
      <div className="flex-1 overflow-auto p-3 space-y-2 text-caption">
        {entries.length === 0 && (
          <div className="text-massclaw-text-muted/80 font-mono text-caption">
            waiting for the next scenario run…
          </div>
        )}
        {entries.map((e, i) =>
          e.kind === "turn" ? (
            <div key={i} className="rounded border border-massclaw-border/50 bg-massclaw-surface/40 px-3 py-2">
              <div className="flex items-center justify-between">
                <span className="text-micro uppercase tracking-wide text-massclaw-accent-light font-mono">
                  run #{e.run_id} · {e.role} · turn {e.idx}
                </span>
                <span className="text-micro text-massclaw-text-muted font-mono">
                  {new Date(e.ts * 1000).toLocaleTimeString()}
                </span>
              </div>
              <div className="mt-1 text-massclaw-text whitespace-pre-wrap break-words text-caption">
                {e.content}
              </div>
            </div>
          ) : (
            <div
              key={i}
              className="rounded border border-massclaw-border/40 bg-black/30 px-3 py-1.5 font-mono text-micro"
            >
              <span className={`${statusColor(e.status)} mr-2`}>
                {e.status ?? "···"}
              </span>
              <span className="text-massclaw-accent-light mr-2">{e.method.toUpperCase()}</span>
              <span className="text-massclaw-text-muted break-all">{e.url}</span>
            </div>
          ),
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
