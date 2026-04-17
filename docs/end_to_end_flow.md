# MassClaw — End-to-End Flow

How a single user request travels through every layer of MassClaw, plain-language.

---

## 1. What MassClaw does (in one paragraph)

A stock AI agent (Claude, GPT, or anything speaking HTTP JSON) hands MassClaw a plain-English task. MassClaw decomposes the task into a DAG, picks the right specialised agents for each node, executes them in parallel while tracking a budget, critiques every output with a reflection engine, pauses for a human when policy says it must, and writes every decision as a signed record into a shared memory layer that replicates across three federated nodes over a CRDT gossip protocol. Any node in the federation can resume any paused workflow — which is the "kill the originator, finish on a peer" demo.

## 2. The eight-step request lifecycle

```
USER                                                                 MASSCLAW
────────────────────────────────────────────────────────────────────────────
 │                                                                        
 ├─1─► POST /api/v1/workflows/submit   {"instruction": "…", "budget": 50}
 │                                                                        
 │        ┌─ Workflow row created, status=PENDING ────────────────────┐   
 │        │ DAG planned by GoalInterpreter + AdaptivePlanner          │   
 │        │ Agents picked by AgentSelector (trust×cost×latency)       │   
 │        └────────────────────────────────────────────────────────────┘  
 │                                                                        
 ├◄─2── 201 Created, workflow_id                                          
 │                                                                        
 │                                                                        
 │                     ┌─ Scheduler.execute_workflow runs on its own ─┐   
 │                     │  loop. For each DAG level:                    │   
 │                     │                                               │   
 │                     │  3. Policy evaluate — registry-based rules    │   
 │                     │       DENY     → task SKIPPED, audit record   │   
 │                     │       ESCALATE → HITL approval gate below     │   
 │                     │       ALLOW    → continue                     │   
 │                     │                                               │   
 │                     │  4. Approval gate (risky caps + policy-escal) │   
 │                     │       → signed WorkflowCheckpoint persisted   │   
 │                     │       → ApprovalRequest carries its hash      │   
 │                     │       → workflow blocks until human replies   │   
 │                     │         (or any peer hits POST /resume)       │   
 │                     │                                               │   
 │                     │  5. LLM call (Haiku for easy, Sonnet for      │   
 │                     │     complex), output → shared memory          │   
 │                     │                                               │   
 │                     │  6. Reflection engine (cached in Redis by     │   
 │                     │     sha256(goal + outputs)):                  │   
 │                     │       accept  → next task                     │   
 │                     │       retry   → SelfCorrectionEngine rebuilds │   
 │                     │                 prompt, node reset to PENDING │   
 │                     │       re_plan → DynamicReplanner injects alt  │   
 │                     │       abort   → signed checkpoint + break     │   
 │                     └───────────────────────────────────────────────┘  
 │                                                                        
 ├─7─► GET /api/v1/workflows/{id}/status    (poll OR)                     
 ├─7─► GET /api/v1/workflows/{id}/stream    (SSE — real-time progress)    
 │                                                                        
 ├─8─► GET /api/v1/workflows/{id}/result    (final synthesized answer)    
 │                                                                        
```

## 3. What makes it decentralized (three-line version)

- **Every memory write is a signed, content-addressed record.** Hash = address. Ed25519 signature proves authorship. Duplicate hashes collapse to one row.
- **A background Celery task on every node gossips** 256 Merkle bucket digests to two random peers every 5–30 s. Mismatched buckets trigger a pull. No coordinator.
- **Checkpoints, approval pointers, and policy decisions are memory records too.** So they gossip. So any node can pick up where another left off.

## 4. What the stock agent sees

When OpenClaw (or any stock agent) lands on a MassClaw node it sees:

1. `/.well-known/agent-facts.json` — a signed AgentFacts v1 document describing the node: skills, endpoints, DID, example requests, example errors.
2. `POST /api/v1/workflows/submit` — the one endpoint it has to call.
3. Every error response is a uniform envelope with `error_code`, `detail`, and a `next_steps` hint telling it exactly what to do next.
4. SSE stream of progress events at `/workflows/{id}/stream` — each event has a `type` field so the agent can pattern-match.

No MassClaw-specific prompt tuning needed. That's the hackathon bet.

## 5. What happens in the five demo scenarios

| # | Scenario | What proves what |
|---|---|---|
| 1 | Stock agent + URL completes a task | Pillar A (identity) + C (HITL path) work |
| 2 | Three nodes, partition one, conflicting writes, heal | Pillar B (CRDT) converges |
| 3 | $4,200 wire → HITL → judge writes a rule live | Pillar D (policy) is hot-reloadable |
| 4 | Kill node-A mid-workflow, approve on node-B, finishes | Architecture is decentralized *in practice* |
| 5 | `/audit` shows a chronological chain for every action | Audit is architectural, not bolted on |

## 6. Diagram — the federation at rest

```
                         NANDA Index (MIT-hosted)
                                  │
                ┌─────────────────┼─────────────────┐
                │                 │                 │
          node-A (us-east)   node-B (eu)      node-C (ap)
          ┌────────────┐    ┌────────────┐    ┌────────────┐
          │ FastAPI    │    │ FastAPI    │    │ FastAPI    │
          │ Celery×2   │    │ Celery×2   │    │ Celery×2   │
          │ Postgres   │    │ Postgres   │    │ Postgres   │
          │ Redis      │    │ Redis      │    │ Redis      │
          └─────┬──────┘    └─────┬──────┘    └─────┬──────┘
                │                 │                 │
                └─────────── CRDT gossip ───────────┘
                       (every 5 s / 30 s)
                   - Merkle bucket summary
                   - diff pull
                   - Ed25519 peer-auth on every request
```

## 7. Where each layer's code lives

| Concern | Backend path |
|---|---|
| Identity + AgentFacts | `backend/app/identity/`, `backend/app/api/well_known.py`, `backend/app/api/identity.py` |
| CRDT shared memory | `backend/app/crdt/`, `backend/app/models/memory.py`, `backend/app/api/memory.py`, `backend/app/api/memory_sync.py` |
| Scheduler + DAG | `backend/app/orchestration/` — `scheduler.py`, `dag.py`, `selector.py`, `synthesizer.py`, `checkpoint.py`, `resume.py` |
| Adaptive intelligence | `backend/app/intelligence/` — `reflection.py`, `self_correction.py`, `dynamic_replanner.py` |
| HITL + approvals | `backend/app/safety/approval.py`, `backend/app/api/approvals.py` |
| Policy engine (new) | `backend/app/safety/registry.py`, `backend/app/safety/decision.py`, `backend/app/safety/context.py`, `backend/app/safety/rules/` |
| Signed audit | `backend/app/safety/audit.py`, `backend/app/api/audit.py` (see `/policy-decisions`) |
| Gossip + workers | `backend/app/workers/`, `backend/app/crdt/gossip.py`, `backend/app/celery_app.py` |

Frontend pages (all in `frontend/src/app/`):

| Page | What it does |
|---|---|
| `/` | Dashboard — agent count, workflow count, system metrics |
| `/agents` + `/agents/[id]` | Registry browser + per-agent AgentFacts panel + verify button |
| `/missions` + `/missions/[id]` | Start + track workflows (the "plan a dinner"-style UI) |
| `/memory` | Search shared memory with semantic query |
| `/approvals` | HITL queue — approve / deny / preview checkpoint |
| `/policy` | Live rule table with toggles + JSON dry-run tester |
| `/audit` | Event log (legacy) + Policy Decisions (signed records) |
| `/trust`, `/wallet`, `/evolution`, `/tools`, `/mcp` | Layer-specific read views |
| `/planning`, `/intelligence` | Reasoning-trace browser |
