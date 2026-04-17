"use client";

import type { FixCommit } from "../types";

type Props = {
  commits: FixCommit[];
};

const GITHUB_BASE = "https://github.com/DeepakKTS/MassClaw/commit/";

export function FixCommitTicker({ commits }: Props) {
  return (
    <div className="glass rounded-lg border border-massclaw-border flex flex-col max-h-60 overflow-hidden">
      <div className="px-4 py-3 border-b border-massclaw-border flex items-center justify-between">
        <span className="text-title font-semibold text-massclaw-text">Auto-fix commits</span>
        <span className="text-caption text-massclaw-text-muted">most recent first</span>
      </div>
      <div className="overflow-auto divide-y divide-massclaw-border">
        {commits.length === 0 && (
          <div className="px-4 py-3 text-caption text-massclaw-text-muted/80 font-mono">
            no fixes yet — failing scenarios will patch MassClaw automatically.
          </div>
        )}
        {commits.map((c) => (
          <a
            key={c.sha}
            href={`${GITHUB_BASE}${c.sha}`}
            target="_blank"
            rel="noreferrer"
            className="block px-4 py-2 hover:bg-massclaw-accent/5 transition"
          >
            <div className="flex items-center justify-between">
              <span className="font-mono text-micro text-massclaw-accent-light">
                {c.sha.slice(0, 10)}
              </span>
              <span className="text-micro text-massclaw-text-muted font-mono">
                {c.scenario ?? "—"} · {new Date(c.ts * 1000).toLocaleTimeString()}
              </span>
            </div>
            <div className="text-caption text-massclaw-text truncate mt-0.5">
              {c.message}
            </div>
          </a>
        ))}
      </div>
    </div>
  );
}
