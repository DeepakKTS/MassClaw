export type ProbeRow = {
  id: number;
  ts: number;
  node_a_root: string | null;
  node_b_root: string | null;
  node_c_root: string | null;
  in_sync: number;
};

export type NodeInfo = {
  name: string;
  url: string;
  log_tail: string[];
};

export type NodesSnapshot = {
  nodes: NodeInfo[];
  latest_probe: ProbeRow | null;
};

export type RecentRun = {
  id: number;
  started_at: number;
  ended_at: number | null;
  outcome: string | null;
  failure_class: string | null;
};

export type RecentRunsMap = Record<string, RecentRun[]>;

export type FixCommit = {
  sha: string;
  scenario: string | null;
  message: string;
  ts: number;
};

export type StreamEvent =
  | { topic: "hello"; payload: { latest_probe: ProbeRow | null } }
  | { topic: "docker.logline"; payload: { node: string; line: string } }
  | { topic: "federation.probe"; payload: { roots: Record<string, string | null>; in_sync: boolean; ts: number } }
  | { topic: "federation.probe.error"; payload: { error: string } }
  | { topic: "cycle.started"; payload: { cycle_id: number; note: string } }
  | { topic: "cycle.closed"; payload: { cycle_id: number; summary: string } }
  | { topic: "scenario.started"; payload: { run_id: number; scenario: string; cycle_id: number | null } }
  | { topic: "scenario.passed"; payload: { run_id: number; outcome: string; failure_class: string | null } }
  | { topic: "scenario.failed"; payload: { run_id: number; outcome: string; failure_class: string | null } }
  | { topic: "agent.turn"; payload: { run_id: number; idx: number; role: string; content: string } }
  | { topic: "agent.http"; payload: { run_id: number; method: string; url: string; status: number | null } }
  | { topic: "fix.committed"; payload: { sha: string; scenario: string | null; message: string } };

export type TranscriptEntry =
  | { kind: "turn"; run_id: number; idx: number; role: string; content: string; ts: number }
  | { kind: "http"; run_id: number; method: string; url: string; status: number | null; ts: number };

export const SCENARIO_LABELS: Record<string, string> = {
  s1: "Discover",
  s2: "Verify identity",
  s3: "Submit workflow",
  s4: "Query memory",
  s5: "Use a tool",
  s6: "Fact resolve modes",
  s7: "Federation sync",
  s8: "HITL approval",
  s9: "Wallet budget",
};
