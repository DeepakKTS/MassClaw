"use client";

import type { ProbeRow } from "../types";

type Props = {
  probe: ProbeRow | null;
};

const CHIPS: { key: keyof ProbeRow; label: string }[] = [
  { key: "node_a_root", label: "node-a" },
  { key: "node_b_root", label: "node-b" },
  { key: "node_c_root", label: "node-c" },
];

export function ConvergenceStrip({ probe }: Props) {
  const inSync = probe?.in_sync === 1;

  return (
    <div className="glass rounded-lg border border-massclaw-border px-4 py-3 flex items-center justify-between gap-4">
      <div className="flex items-center gap-3">
        <span
          className={`inline-flex items-center gap-2 px-3 py-1 rounded-full text-caption font-mono ${
            inSync
              ? "bg-massclaw-success/15 text-massclaw-success"
              : "bg-massclaw-warning/15 text-massclaw-warning"
          }`}
        >
          <span className="h-2 w-2 rounded-full bg-current" />
          {inSync ? "in sync" : "diverged"}
        </span>
        <span className="text-caption text-massclaw-text-muted">
          {probe ? `probe #${probe.id} · ${new Date(probe.ts * 1000).toLocaleTimeString()}` : "waiting for first probe"}
        </span>
      </div>
      <div className="flex items-center gap-2">
        {CHIPS.map((chip) => {
          const root = probe ? (probe[chip.key] as string | null) : null;
          return (
            <div
              key={chip.label}
              className="flex items-center gap-2 px-3 py-1 rounded border border-massclaw-border bg-massclaw-surface/40"
              title={root ?? "no data"}
            >
              <span className="text-micro font-mono text-massclaw-text-muted">{chip.label}</span>
              <span className="text-micro font-mono text-massclaw-text">
                {root ? `${root.slice(0, 12)}…` : "—"}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
