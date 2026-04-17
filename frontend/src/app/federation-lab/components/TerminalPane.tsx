"use client";

import { useEffect, useRef } from "react";

const ANSI_RE = /\u001b\[[0-9;]*[A-Za-z]/g;

function stripAnsi(line: string): string {
  return line.replace(ANSI_RE, "");
}

type Props = {
  nodeName: string;
  url: string;
  merkleRoot: string | null;
  inSync: boolean;
  lines: string[];
};

export function TerminalPane({ nodeName, url, merkleRoot, inSync, lines }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [lines.length]);

  return (
    <div className="glass rounded-lg border border-massclaw-border overflow-hidden flex flex-col h-full">
      <div className="flex items-center justify-between px-3 py-2 border-b border-massclaw-border bg-massclaw-surface/60">
        <div className="flex items-center gap-2">
          <span className={`inline-block h-2 w-2 rounded-full ${inSync ? "bg-massclaw-success" : "bg-massclaw-warning"}`} />
          <span className="font-mono text-caption text-massclaw-text">{nodeName}</span>
          <span className="text-massclaw-text-muted text-caption">·</span>
          <span className="font-mono text-micro text-massclaw-text-muted">{url}</span>
        </div>
        <div className="font-mono text-micro text-massclaw-text-muted">
          root: {merkleRoot ? `${merkleRoot.slice(0, 10)}…` : "—"}
        </div>
      </div>
      <div className="flex-1 overflow-auto p-3 font-mono text-micro leading-5 text-massclaw-text-muted bg-black/40">
        {lines.length === 0 ? (
          <div className="text-massclaw-text-muted/60">awaiting log lines…</div>
        ) : (
          lines.map((line, i) => (
            <div key={i} className="whitespace-pre-wrap break-all">
              {stripAnsi(line)}
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
