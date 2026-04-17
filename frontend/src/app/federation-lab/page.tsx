"use client";

import { useCallback, useEffect, useMemo, useReducer, useState } from "react";

import { AgentTranscript } from "./components/AgentTranscript";
import { ConvergenceStrip } from "./components/ConvergenceStrip";
import { FixCommitTicker } from "./components/FixCommitTicker";
import { ScenarioGrid } from "./components/ScenarioGrid";
import { TerminalPane } from "./components/TerminalPane";
import { useHarnessStream } from "./hooks/useHarnessStream";
import type {
  FixCommit,
  NodesSnapshot,
  ProbeRow,
  RecentRunsMap,
  StreamEvent,
  TranscriptEntry,
} from "./types";

const MAX_LOG_LINES_PER_NODE = 400;
const MAX_TRANSCRIPT_ENTRIES = 300;
const MAX_COMMITS = 30;

type State = {
  snapshot: NodesSnapshot | null;
  logsByNode: Record<string, string[]>;
  probe: ProbeRow | null;
  recent: RecentRunsMap;
  runningRunIds: Record<string, number | null>;
  transcript: TranscriptEntry[];
  commits: FixCommit[];
};

type Action =
  | { type: "snapshot"; snapshot: NodesSnapshot }
  | { type: "recent"; recent: RecentRunsMap }
  | { type: "commits"; commits: FixCommit[] }
  | { type: "event"; event: StreamEvent };

const initialState: State = {
  snapshot: null,
  logsByNode: { "node-a": [], "node-b": [], "node-c": [] },
  probe: null,
  recent: {},
  runningRunIds: {},
  transcript: [],
  commits: [],
};

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "snapshot": {
      const logsByNode: Record<string, string[]> = { ...state.logsByNode };
      for (const node of action.snapshot.nodes) {
        logsByNode[node.name] = node.log_tail.slice(-MAX_LOG_LINES_PER_NODE);
      }
      return {
        ...state,
        snapshot: action.snapshot,
        logsByNode,
        probe: action.snapshot.latest_probe,
      };
    }
    case "recent":
      return { ...state, recent: action.recent };
    case "commits":
      return { ...state, commits: action.commits };
    case "event": {
      const { event } = action;
      switch (event.topic) {
        case "hello":
          return { ...state, probe: event.payload.latest_probe };
        case "docker.logline": {
          const { node, line } = event.payload;
          const existing = state.logsByNode[node] ?? [];
          const next = existing.concat(line).slice(-MAX_LOG_LINES_PER_NODE);
          return { ...state, logsByNode: { ...state.logsByNode, [node]: next } };
        }
        case "federation.probe": {
          const nextProbe: ProbeRow = {
            id: state.probe ? state.probe.id + 1 : 1,
            ts: event.payload.ts,
            node_a_root: event.payload.roots["node-a"] ?? null,
            node_b_root: event.payload.roots["node-b"] ?? null,
            node_c_root: event.payload.roots["node-c"] ?? null,
            in_sync: event.payload.in_sync ? 1 : 0,
          };
          return { ...state, probe: nextProbe };
        }
        case "scenario.started": {
          return {
            ...state,
            runningRunIds: {
              ...state.runningRunIds,
              [event.payload.scenario]: event.payload.run_id,
            },
          };
        }
        case "scenario.passed":
        case "scenario.failed": {
          const next: Record<string, number | null> = { ...state.runningRunIds };
          for (const [key, val] of Object.entries(next)) {
            if (val === event.payload.run_id) next[key] = null;
          }
          return { ...state, runningRunIds: next };
        }
        case "agent.turn": {
          const entry: TranscriptEntry = {
            kind: "turn",
            run_id: event.payload.run_id,
            idx: event.payload.idx,
            role: event.payload.role,
            content: event.payload.content,
            ts: Date.now() / 1000,
          };
          return {
            ...state,
            transcript: state.transcript.concat(entry).slice(-MAX_TRANSCRIPT_ENTRIES),
          };
        }
        case "agent.http": {
          const entry: TranscriptEntry = {
            kind: "http",
            run_id: event.payload.run_id,
            method: event.payload.method,
            url: event.payload.url,
            status: event.payload.status,
            ts: Date.now() / 1000,
          };
          return {
            ...state,
            transcript: state.transcript.concat(entry).slice(-MAX_TRANSCRIPT_ENTRIES),
          };
        }
        case "fix.committed": {
          const next: FixCommit = {
            sha: event.payload.sha,
            scenario: event.payload.scenario,
            message: event.payload.message,
            ts: Date.now() / 1000,
          };
          return { ...state, commits: [next, ...state.commits].slice(0, MAX_COMMITS) };
        }
        default:
          return state;
      }
    }
    default:
      return state;
  }
}

export default function FederationLabPage() {
  const [state, dispatch] = useReducer(reducer, initialState);
  const [error, setError] = useState<string | null>(null);

  const onEvent = useCallback((event: StreamEvent) => {
    dispatch({ type: "event", event });
  }, []);

  const { connection, harnessBase } = useHarnessStream(onEvent);

  useEffect(() => {
    let cancelled = false;

    async function loadInitial() {
      try {
        const [nodesRes, recentRes, fixesRes] = await Promise.all([
          fetch(`${harnessBase}/harness/nodes`),
          fetch(`${harnessBase}/harness/scenarios/recent`),
          fetch(`${harnessBase}/harness/fixes?limit=30`),
        ]);
        if (!nodesRes.ok || !recentRes.ok || !fixesRes.ok) {
          throw new Error("harness not reachable");
        }
        const snapshot = (await nodesRes.json()) as NodesSnapshot;
        const recent = (await recentRes.json()) as RecentRunsMap;
        const commits = (await fixesRes.json()) as FixCommit[];
        if (cancelled) return;
        dispatch({ type: "snapshot", snapshot });
        dispatch({ type: "recent", recent });
        dispatch({ type: "commits", commits });
      } catch (e) {
        if (cancelled) return;
        setError("Harness unreachable at " + harnessBase + " — run `make harness-up` from repo root.");
      }
    }

    void loadInitial();
    const interval = setInterval(() => void loadInitial(), 30000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [harnessBase]);

  const nodes = state.snapshot?.nodes ?? [
    { name: "node-a", url: "http://localhost:18001", log_tail: [] },
    { name: "node-b", url: "http://localhost:18002", log_tail: [] },
    { name: "node-c", url: "http://localhost:18003", log_tail: [] },
  ];

  const merkleByNode: Record<string, string | null> = useMemo(
    () => ({
      "node-a": state.probe?.node_a_root ?? null,
      "node-b": state.probe?.node_b_root ?? null,
      "node-c": state.probe?.node_c_root ?? null,
    }),
    [state.probe],
  );

  const inSync = state.probe?.in_sync === 1;

  return (
    <div className="mx-auto max-w-[1400px] space-y-4">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-heading font-semibold text-massclaw-text">
            Federation Lab
          </h1>
          <p className="text-caption text-massclaw-text-muted mt-1">
            Local 3-node MassClaw + OpenClaw hardening harness. Zero cloud, zero extra spend.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span
            className={`inline-flex items-center gap-2 px-3 py-1 rounded-full text-micro font-mono border ${
              connection === "open"
                ? "border-massclaw-success text-massclaw-success"
                : connection === "connecting"
                  ? "border-massclaw-warning text-massclaw-warning"
                  : "border-massclaw-danger text-massclaw-danger"
            }`}
          >
            <span className="h-2 w-2 rounded-full bg-current" />
            harness {connection}
          </span>
        </div>
      </div>

      {error && (
        <div className="glass rounded-lg border border-massclaw-danger/60 px-4 py-3 text-caption text-massclaw-danger">
          {error}
        </div>
      )}

      <ConvergenceStrip probe={state.probe} />

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 h-[300px]">
        {nodes.map((node) => (
          <TerminalPane
            key={node.name}
            nodeName={node.name}
            url={node.url}
            merkleRoot={merkleByNode[node.name]}
            inSync={inSync}
            lines={state.logsByNode[node.name] ?? []}
          />
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <div className="lg:col-span-2 space-y-4">
          <ScenarioGrid recent={state.recent} runningRunIds={state.runningRunIds} />
          <FixCommitTicker commits={state.commits} />
        </div>
        <div className="lg:col-span-3 min-h-[400px]">
          <AgentTranscript entries={state.transcript} />
        </div>
      </div>
    </div>
  );
}
