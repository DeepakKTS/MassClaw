"use client";

import type { RecentRun, RecentRunsMap } from "../types";
import { SCENARIO_LABELS } from "../types";

type Props = {
  recent: RecentRunsMap;
  runningRunIds: Record<string, number | null>;
};

function Dot({ run }: { run: RecentRun | null }) {
  if (!run) {
    return (
      <span className="h-2.5 w-2.5 rounded-full border border-massclaw-border bg-transparent" />
    );
  }
  if (!run.ended_at) {
    return <span className="h-2.5 w-2.5 rounded-full bg-massclaw-accent animate-pulse" />;
  }
  const isPass = run.outcome === "passed";
  return (
    <span
      className={`h-2.5 w-2.5 rounded-full ${
        isPass ? "bg-massclaw-success" : "bg-massclaw-danger"
      }`}
      title={`run #${run.id} — ${run.outcome ?? "unknown"}${run.failure_class ? ` · ${run.failure_class}` : ""}`}
    />
  );
}

export function ScenarioGrid({ recent, runningRunIds }: Props) {
  const scenarios = Object.keys(SCENARIO_LABELS);
  return (
    <div className="glass rounded-lg border border-massclaw-border">
      <div className="px-4 py-3 border-b border-massclaw-border flex items-center justify-between">
        <span className="text-title font-semibold text-massclaw-text">Scenarios</span>
        <span className="text-caption text-massclaw-text-muted">last 5 runs each</span>
      </div>
      <div className="divide-y divide-massclaw-border">
        {scenarios.map((s) => {
          const runs = recent[s] ?? [];
          const runningId = runningRunIds[s];
          const mostRecent = runs[0];
          const isFailing = mostRecent && mostRecent.outcome === "failed";
          return (
            <div
              key={s}
              className={`flex items-center justify-between px-4 py-2 ${
                isFailing ? "ring-1 ring-massclaw-accent/60" : ""
              }`}
            >
              <div className="flex items-center gap-3">
                <span className="font-mono text-caption text-massclaw-text-muted w-6">{s}</span>
                <span className="text-body text-massclaw-text">{SCENARIO_LABELS[s]}</span>
              </div>
              <div className="flex items-center gap-2">
                {runningId != null && (
                  <span className="h-2.5 w-2.5 rounded-full bg-massclaw-accent animate-pulse" title={`running #${runningId}`} />
                )}
                {Array.from({ length: 5 }).map((_, i) => (
                  <Dot key={i} run={runs[i] ?? null} />
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
