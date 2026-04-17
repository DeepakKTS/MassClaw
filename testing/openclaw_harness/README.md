# OpenClaw Hardening Harness

Local contract-test lab for MassClaw. Runs a three-node federation locally,
simulates a stock OpenClaw agent, logs every interaction, and auto-fixes bugs
as they surface. No container runtime required; uses Homebrew Postgres + Redis
directly.

## Architecture

Three moving pieces:

1. **Native 3-node MassClaw federation** — three uvicorn processes on ports
   18001/18002/18003, each backed by its own Postgres database
   (`massclaw_fed_a/_b/_c`) and Redis DB index (10/11/12). Brought up by
   `scripts/dev_federation_up.sh`.

2. **Harness server (this package)** — FastAPI on `:19000`. State + telemetry
   only. Receives scenario results from the Claude Code orchestrator via POSTs
   and broadcasts them over WebSocket to the dashboard. Tails the three node
   log files and polls Merkle convergence every 5 seconds.

3. **Claude Code session (orchestrator)** — spawns restricted subagents that
   pretend to be stock OpenClaw agents and run the 9 scenarios against
   MassClaw. When a subagent fails, Claude Code patches the offending code,
   commits, pushes. All LLM work rides the user's Max subscription; the
   harness server itself makes no LLM calls.

## Running

Prereqs: Python 3.12 conda env `massclaw-fed` (created from
`backend/pyproject.toml`), Postgres 17 running locally, Redis running locally.

```bash
# 1. Bring up 3-node federation (native processes)
cd ..             # repo root
make fed-native-up

# 2. Start harness server
cd testing/openclaw_harness
make harness-up

# 3. Start Next.js frontend (in another terminal)
cd ../../frontend && npm run dev

# 4. Open dashboard
open http://localhost:3000/federation-lab
```

To run a scenario manually, ask Claude Code:

> "please run openclaw scenario s3 now"

To run continuous cycles (auto-rotation through scenarios every 15 minutes):

> "/loop 15m run openclaw cycle"

Stop the cycle with `/loop stop` in Claude Code.

## File map

- `harness/openclaw.md` — the single instructions page handed to the simulated
  OpenClaw and (on demo day) the real one. **This is the Phase 1 deliverable**;
  if a scenario fails because the simulated agent couldn't follow this doc,
  the doc — not the backend — is usually what needs fixing.
- `harness/simulated_agent.md` — system prompt template that restricts each
  subagent to HTTP + re-reading `openclaw.md`.
- `harness/scenarios/s1–s9.py` — one file per scenario, each exporting a
  `goal` string and a server-side `grade(http_calls, narrative)` function.
- `harness/server.py` — FastAPI on :19000.
- `harness/state.py` — SQLite schema.
- `harness/events.py` — in-process pub/sub.
- `harness/process_logs.py` — tails the three uvicorn log files.
- `harness/federation_probe.py` — polls Merkle roots across the three nodes.
- `harness/config.py` — ports, paths, cadence, safety allowlist.

## Safety tripwires

- **Three consecutive failed patch attempts** on the same scenario → I stop,
  write `escalations/<ts>-<scenario>.md`, dashboard shows red banner.
- **Safety allowlist** — any auto-fix touching `.github/workflows/`,
  deleting anything under `alembic/versions/`, or changing `deploy/fly/` → I
  stop and escalate without committing.

## Zero-spend policy

This harness spends **$0 incremental**. All LLM work happens through the
parent Claude Code session on the user's Claude 20x Max plan. No
anthropic/openai/groq SDK dependencies are installed; this is enforced by
`pyproject.toml`.
