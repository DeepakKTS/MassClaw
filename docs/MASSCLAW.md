# MassClaw — Full Architecture, Flow & Test Reference

**The master document. Second brain for the whole project.** Every subsystem, every file worth knowing, every endpoint, every test you should run.

Pair this with the live status board at `file:///Users/deepakzedler/Documents/MassClaw/massclaw_dashboard.html`.

Last rebuilt: 2026-04-17.

---

## Table of contents

1. [What MassClaw is](#1-what-massclaw-is)
2. [Why — the three problems it solves](#2-why--the-three-problems-it-solves)
3. [The end-to-end request flow](#3-the-end-to-end-request-flow)
4. [Architecture — 30-second read](#4-architecture--30-second-read)
5. [The eight core layers](#5-the-eight-core-layers)
6. [NANDA-native identity + CRDT federation](#6-nanda-native-identity--crdt-federation)
7. [Adaptive intelligence (reflection, self-correction, re-plan)](#7-adaptive-intelligence)
8. [HITL + checkpoints + cross-node resume](#8-hitl--checkpoints--cross-node-resume)
9. [Policy engine (registry + 10 rules + signed audit)](#9-policy-engine)
10. [Backend file-by-file map](#10-backend-file-by-file-map)
11. [API catalog](#11-api-catalog)
12. [Frontend catalog](#12-frontend-catalog)
13. [Database models](#13-database-models)
14. [Workers + scheduled tasks](#14-workers--scheduled-tasks)
15. [Middleware + error envelope](#15-middleware--error-envelope)
16. [Federation compose](#16-federation-compose)
17. [Testing strategy (automated + manual + chaos)](#17-testing-strategy)
18. [Section-by-section test plan](#18-section-by-section-test-plan)
19. [Edge case catalog](#19-edge-case-catalog)
20. [How to run the whole thing](#20-how-to-run-the-whole-thing)
21. [Open questions + future work](#21-open-questions--future-work)

---

## 1. What MassClaw is

**MassClaw is a NANDA-native federated operating layer for AI agents.** A stock agent (Claude, GPT, or anything speaking HTTP JSON) can hand MassClaw a plain-English task. MassClaw:

- **Decomposes** it into a DAG of subtasks
- **Selects** the best specialised agents for each node (trust × cost × latency)
- **Executes** them in parallel while tracking a budget
- **Critiques** every output with a reflection engine and retries / re-plans / aborts as needed
- **Pauses for a human** when a policy rule says so
- **Produces a signed, auditable receipt** for every decision
- **Replicates** all of that state across a three-node federation via CRDT gossip — so if any node dies, another can resume the work

The elevator pitch: *the OS for the Internet of AI Agents.*

## 2. Why — the three problems it solves

1. **"Shared memory" in today's agent systems isn't actually decentralised.** Most implementations are a single Postgres + Redis cache. MassClaw's memory is a signed, content-addressed CRDT that gossips across peers with no coordinator.
2. **Agents aren't speaking any common protocol.** MassClaw speaks NANDA natively — every agent gets an AgentFacts v1 document with a `did:key:` identifier, discoverable via `/.well-known/agent-facts.json` and registerable with the MIT NANDA Index.
3. **Adaptive intelligence + HITL are usually bolted on as demos.** Here they're wired into the scheduler's main loop: policy evaluation, signed audit, approval gate, reflection dispatch, retry, re-plan, abort. Eight call-sites in `scheduler.py`, all covered by tests.

## 3. The end-to-end request flow

```
 USER / STOCK AGENT                                                    MASSCLAW
───────────────────────────────────────────────────────────────────────────────
 │
 ├─① POST /api/v1/workflows/submit  {"instruction": "…", "budget": 50}
 │       │
 │       ▼
 │   WorkflowService.create → status=PENDING, Workflow row
 │
 ├─② 201 Created, workflow_id
 │
 │
 │                       ╔═══════════════════════════════════════════╗
 │                       ║  WorkflowScheduler.execute_workflow loop  ║
 │                       ╚═══════════════════════════════════════════╝
 │
 │                       ③ _policy_evaluate_nodes                Day-15/16
 │                          └─ 10 built-in rules, aggregate()
 │                          └─ DENY → task SKIPPED, signed audit
 │                          └─ ESCALATE → route to HITL gate
 │                          └─ ALLOW / ABSTAIN → continue
 │                          └─ record_decision writes signed memory
 │
 │                       ④ Approval gate                         Day-13
 │                          └─ CheckpointStore.save (signed META)
 │                          └─ ApprovalRequest with checkpoint_hash
 │                          └─ EventBus publish "approval.requested"
 │                          └─ block on Redis polling OR any peer's
 │                             POST /workflows/{id}/resume
 │
 │                       ⑤ LLM call via ModelRouter              Day-∞
 │                          └─ Haiku (cheap) or Sonnet (complex)
 │                          └─ Output → signed memory record
 │
 │                       ⑥ _reflect_on_task                      Day-12/14
 │                          ├─ ReflectionEngine (Redis cache)
 │                          ├─ accept  → continue
 │                          ├─ retry_task → SelfCorrectionEngine
 │                          │              rebuild prompt, DAG mark_pending
 │                          ├─ re_plan  → DynamicReplanner inject alt node
 │                          └─ abort   → CheckpointStore.save
 │
 │                       ╚══════════════════════════════════════════╝
 │
 ├─⑦ GET /workflows/{id}/stream  (Server-Sent Events stream)
 │
 ├─⑧ GET /workflows/{id}/result  (final synthesized answer)
```

Every step writes to the signed, content-addressed memory layer. That layer gossips across peers at 5 – 30 s intervals. That is what makes the architecture federated in practice, not just on paper.

## 4. Architecture — 30-second read

```
              NANDA Index (MIT-hosted)
                      │
         ┌────────────┼────────────┐
         │            │            │
     node-A       node-B       node-C
     ┌───────┐   ┌───────┐   ┌───────┐
     │FastAPI│   │FastAPI│   │FastAPI│
     │Celery │   │Celery │   │Celery │
     │PG+pgv │   │PG+pgv │   │PG+pgv │
     │Redis  │   │Redis  │   │Redis  │
     └───┬───┘   └───┬───┘   └───┬───┘
         │           │           │
         └─── CRDT gossip ───────┘
              every 5–30 s
```

- **Single node** ≈ 1 FastAPI + 1 Celery worker+beat + Postgres(+pgvector) + Redis.
- **Federation** = three identical nodes peered via gossip. State survives any single-node death.
- **Identity** is Ed25519 / `did:key`. Every record, checkpoint, approval, policy decision is signed.
- **Memory** is content-addressed (SHA-256 of canonical body). Duplicates collapse to one row.

## 5. The eight core layers

Each layer is independently usable; all eight work together through the scheduler.

| Layer | What it does | Key code |
|---|---|---|
| **Registry** | Agent discovery + capability matching | `app/api/agents.py`, `app/services/agent_service.py`, `app/models/agent.py` |
| **Trust** | Bayesian trust scoring w/ decay — bad agents fade, good ones rise | `app/services/trust_service.py`, `app/models/trust.py`, `app/workers/trust_decay.py` |
| **Memory** | Signed, content-addressed CRDT records w/ three-mode fact resolution | `app/crdt/`, `app/services/memory_service.py`, `app/services/memory_fact_resolver.py` |
| **Orchestration** | DAG scheduler w/ checkpoint, resume, reflection, self-correction, replan | `app/orchestration/`, `app/intelligence/` |
| **Wallet** | Per-workflow reserve / charge / release ledger | `app/services/wallet_service.py`, `app/models/wallet.py` |
| **Safety** | Policy engine + 10 built-in rules + content filter + injection detector + HITL approvals | `app/safety/`, `app/safety/rules/`, `app/api/approvals.py` |
| **Evolution** | Promote/demote agents based on composite score | `app/services/evolution_service.py`, `app/workers/score_update.py` |
| **Audit** | Every decision → signed memory record + event-bus publish | `app/safety/audit.py`, `app/api/audit.py`, `app/services/audit_service.py` |

Plus two cross-cutting primitives:

- **Tools** — `app/tools/`: in-process tools (code_execute, file I/O, web_search, api_caller) and sandboxed runners.
- **Protocols** — `app/protocols/`: HTTP + MCP adapter, message bus, tool registry.

## 6. NANDA-native identity + CRDT federation

### Identity — `app/identity/`

| File | Purpose |
|---|---|
| `signer.py` | Ed25519 `generate_keypair`, `sign_bytes`, `verify_bytes`, multibase base58btc encode/decode |
| `key_store.py` | Encrypted-at-rest instance keypair via ChaCha20-Poly1305 KEK. Default path probes `/var/lib/massclaw` → falls back to `~/.massclaw`; override with `IDENTITY_INSTANCE_KEY_PATH` |
| `agent_facts.py` | Builds + verifies NANDA v1 AgentFacts JSON-LD with DataIntegrityProof (`eddsa-rdfc-2022`) |
| `did.py` | Parser + builder for `did:key`, `did:web`, `did:nanda` |
| `did_resolver.py` | Resolves a DID to the public key bytes for verification |
| `nanda_index.py` | HTTP client to MIT NANDA Index: `GET /agent/{name}` returns AgentFacts; 1h cache |
| `canonicalize.py` | RFC 8785 JSON canonicalisation — deterministic bytes for signing |
| `well_known_fetcher.py` | Pulls `/.well-known/agent-facts.json` from peer agents |

### CRDT — `app/crdt/`

| File | Purpose |
|---|---|
| `store.py` | `CRDTStore.put` — pre-checks hash, uses SAVEPOINT-protected INSERT so duplicate hashes collapse to a no-op even under concurrent writes |
| `hashing.py` | `CanonicalBody` + `compute_content_hash()` — what actually gets signed |
| `merkle.py` | 256-bucket Merkle summary keyed on first byte of each record hash |
| `sync.py` | Pull-on-mismatch algorithm |
| `gossip.py` | Fanout loop called by Celery beat |
| `peer_client.py` | Outbound HTTP client with Ed25519 request signing |
| `peer_auth.py` | Inbound verification of peer signatures |

### Federation — `docker/federation.yml`

12 containers (3 × postgres + redis + backend + worker+beat). Each node runs an identical image with a different `MASSCLAW_FEDERATION_PEERS` env var. Postgres + Redis are tmpfs-backed so demos reset cleanly. Every node owns its own keypair, so records persist their originator's signature even as they propagate.

Demo cadence: `GOSSIP_INTERVAL_SECONDS=5`. Production: 30.

## 7. Adaptive intelligence

### Reflection — `app/intelligence/reflection.py`

After every task completes, the scheduler calls `ReflectionEngine.reflect(goal, outputs)`. It runs a cheap LLM (Sonnet at low temperature) and returns `ReflectionResult(action, confidence, issues, suggestions)` where `action ∈ {accept, retry_task, add_verifier, re_plan, abort}`.

**Cache**: opt-in Redis-backed, keyed on `sha256(goal + outputs)` with 10-minute TTL. On a retry where the output hasn't actually changed, the second reflection call short-circuits — saves real tokens.

### Self-correction — `app/intelligence/self_correction.py`

On `retry_task`, `SelfCorrectionEngine.evaluate(output, reflection, retry_count)` decides whether to retry and what prompt-feedback to add. Respects `Task.retry_count` (DB column, source of truth) + `MAX_RETRIES_PER_TASK=2`. Refuses retry when confidence is already high.

### Dynamic replanning — `app/intelligence/dynamic_replanner.py`

On `re_plan` / `add_verifier`, `DynamicReplanner.replan(dag, failed_node_id, reflection_action, context)` injects a new DAG node marked `{original_id}_alt` with the same capability but a different approach. Keeps the original node in the DAG for audit.

### Scheduler wiring — `app/orchestration/scheduler.py`

| Line | What runs |
|---|---|
| `scheduler.py:278` | `_policy_evaluate_nodes` pre-flight |
| `scheduler.py:302–341` | Approval gate + `CheckpointStore.save` |
| `scheduler.py:678` | `_reflect_on_task` post-task |
| `scheduler.py:862` | `record_decision` signed audit write |
| `scheduler.py:955` | `SelfCorrectionEngine.evaluate` retry branch |
| `scheduler.py:993` | `DynamicReplanner.replan` re_plan branch |
| `scheduler.py:1039` | `WorkflowCheckpoint.save` abort branch |

## 8. HITL + checkpoints + cross-node resume

### Approval model — `app/safety/approval.py`

`ApprovalRequest` is a Pydantic model stored in Redis (5 minute TTL, extendable). Fields: `request_id`, `workflow_id`, `task_id`, `action`, `policy_rule`, `context`, `checkpoint_hash` (Day-13 addition), `status`, `expires_at`, `decided_by`, `decided_at`.

### Checkpoint — `app/orchestration/checkpoint.py`

`WorkflowCheckpoint` serialises the live scheduler state (DAG + task status buckets + reflection context + variables) and persists it as a signed META memory record via `CheckpointStore.save()`. The content hash is what gossips across peers.

### Resume — `app/orchestration/resume.py`

`WorkflowResumer.restore()` is idempotent:

- Loads the checkpoint by content hash
- Re-hydrates DAG via `DAG.from_dict(checkpoint.dag_snapshot)`
- Creates the Workflow row if missing (the "we're a peer that never had the workflow" case)
- Creates / patches Task rows to match the checkpoint buckets
- Returns a `ResumeOutcome` summarising what changed

### API — `POST /api/v1/workflows/{id}/resume`

Validates:
- the `checkpoint_hash` exists in local CRDT memory (or gossiped in from a peer)
- the approval (if `approval_id` is given) is status `approved`
- the checkpoint's `workflow_id` matches the path `{id}` — otherwise 409

Then runs `WorkflowResumer.restore()` and re-enters the scheduler in a background task using `AgentSelector.select_agents_for_dag()` to pick local agents.

**Demo scenario**: kill node-A mid-workflow, approve on node-B, node-B pulls the checkpoint by hash (already gossiped), restores, re-enters scheduler, finishes the work. No central coordinator. Covered end-to-end in `tests/integration/test_orchestration_resume.py`.

## 9. Policy engine

### Registry — `app/safety/registry.py`

Each rule is an `async def` Python function decorated with `@policy_rule(rule_id=..., priority=..., enabled=True)`. The decorator inserts the rule into `PolicyRegistry._rules` (process-global). Rules are toggleable at runtime via `POST /policy/registry/rules/{rule_id}/enable|disable`. `evaluate(ctx, rule_ids=None)` runs them in priority order and short-circuits on `DENY`.

### Decision aggregation — `app/safety/decision.py`

Precedence: **deny > escalate_human > allow > abstain**. `aggregate([Decision])` returns the winning action and a list of `contributing_rule_ids` so the audit trail keeps every voter, not just the winner. Crashing rules become abstain (never sink the evaluation). Non-Decision returns become abstain.

### Context — `app/safety/context.py`

`PolicyContext` is a frozen dataclass: `agent_did`, `agent_trust_score`, `agent_capabilities`, `agent_id`, `action`, `action_category`, `tool_name`, `amount`, `target`, `estimated_cost`, `workflow_id`, `task_id`, `now`, `session` (AsyncSession), `redis` (aioredis), `extra` (dict for rule-specific payload).

### Ten built-in rules — `app/safety/rules/`

| Rule ID | Priority | Trigger | Decision |
|---|---|---|---|
| `trust_floor_for_payments` | 10 | `action_category=="payment"` | trust ≥ 0.6 allow; 0.4 – 0.6 escalate; < 0.4 deny |
| `pii_tool_guards` | 15 | tool_name in PII set OR `action_category=="pii_access"` | always escalate_human |
| `rate_limit_per_tool` | 20 | tool_name + agent_id set + redis available | sliding window (20 / 60s default); over → deny |
| `memory_write_attribution` | 25 | `action=="write_memory"` | deny if `agent_did` missing or bogus |
| `signature_requires` | 26 | `action=="write_memory"` | deny if `extra.signature` empty |
| `cost_caps` | 30 | `estimated_cost` set | < $10 allow, $10 – $50 escalate, ≥ $50 deny |
| `credential_requires` | 35 | Phase-1 opt-in: `extra.require_credential=true` AND sensitive category | deny if missing/expired; allow if fresh |
| `time_of_day_restrictions` | 40 | sensitive category | outside 08 – 20 UTC → escalate_human |
| `cross_agent_delegation` | 50 | `action=="delegate_task"` | depth > 2 → escalate_human |
| `default_deny_unknown_tool` | 999 | `tool_name` set + `extra.known_tools` configured | tool not on allowlist → deny |

### Live toggle flow

```
UI /policy → POST /policy/registry/rules/{id}/disable
             → PolicyRegistry.set_enabled(id, False)
             → next scheduler tick sees the new state (no restart)
```

### Signed audit — `app/safety/audit.py`

`record_decision(session, keypair, decision, ctx)` writes every non-abstain decision as a signed META memory record with metadata markers `policy_decision=True`, full `decision` payload (`to_audit_payload()`), and a side-effect-free `context_summary` (session/redis handles stripped). Abstain decisions are skipped — a rule that didn't speak shouldn't add audit noise.

### Listing — `GET /api/v1/audit/policy-decisions`

Query params: `workflow_id`, `action` (allow / deny / escalate_human), `limit`, `offset`. Returns signed records newest-first with `content_hash`, `author_did`, `decision`, `context`.

## 10. Backend file-by-file map

**`app/orchestration/`** — DAG build / schedule / checkpoint / resume

- `dag.py` — `DAG`, `DAGNode` · topological sort, cycle detection
- `decomposer.py` — `TaskDecomposer` · prompt → DAG via LLM
- `selector.py` — `AgentSelector.select_agents_for_dag()` · trust × cost × latency ranking
- `scheduler.py` — `WorkflowScheduler.execute_workflow` · the main loop
- `synthesizer.py` — `OutputSynthesizer` · stitches task outputs into final result
- `state_machine.py` — TaskStatus transitions
- `checkpoint.py` — `WorkflowCheckpoint` + `CheckpointStore`
- `resume.py` — `WorkflowResumer.restore()` + `ResumeOutcome`

**`app/intelligence/`** — picks strategy + critiques output

- `goal_interpreter.py` · `GoalInterpreter` — prompt → structured goal
- `planner.py` · `AdaptivePlanner` — goal + budget → execution plan
- `strategy_router.py` · `StrategyRouter` — dispatches plan against scheduler
- `reflection.py` · `ReflectionEngine` + Redis cache
- `self_correction.py` · `SelfCorrectionEngine`
- `dynamic_replanner.py` · `DynamicReplanner`
- `guardrails.py` · `ExecutionGuardrails` — budget + latency + confidence limits
- `composer.py` · `FinalAnswerComposer`

**`app/services/`** — 17 domain services:

`agent_service`, `workflow_service`, `task_service`, `memory_service`, `memory_lifecycle`, `memory_fact_resolver`, `trust_service`, `wallet_service`, `policy_service`, `audit_service`, `evolution_service`, `consensus_service`, `health_service`, `identity_service`, `project_service`, `task_test_service`.

**`app/safety/`** — policy + HITL + content

- `registry.py`, `decision.py`, `context.py`, `audit.py`, `approval.py`, `policy_engine.py` (legacy JSON-rule engine), `content_filter.py`, `injection_detector.py`
- `rules/*.py` — the 10 built-ins

**`app/crdt/`** — content-addressed memory + gossip

- `store.py`, `hashing.py`, `merkle.py`, `sync.py`, `gossip.py`, `peer_client.py`, `peer_auth.py`

**`app/identity/`** — Ed25519 + NANDA

- `signer.py`, `key_store.py`, `agent_facts.py`, `did.py`, `did_resolver.py`, `nanda_index.py`, `canonicalize.py`, `well_known_fetcher.py`

**`app/middleware/`** — HTTP interceptors

- `logging.py`, `correlation.py`, `rate_limit.py`, `body_size.py`, `error_handler.py`

**`app/core/`** — infra

- `database.py`, `redis.py`, `logging.py`, `circuit_breaker.py`, `retry.py`, `events.py`, `security.py`, `errors.py` (envelope builder)

**`app/workers/`** — Celery

- `celery_app.py`, `health_check.py`, `trust_decay.py`, `memory_gc.py`, `score_update.py`, `crdt_gossip.py`

**`app/tools/`** — tool registry + executors

- `base.py`, `code_execute.py`, `file_io.py`, `web_search.py`, `api_caller.py`, `registry.py`, `executor.py`

**`app/protocols/`** — protocol adapters

- `base.py`, `http_adapter.py`, `mcp_client.py`, `mcp_server.py`, `message_bus.py`, `tool_registry.py`

**`app/embeddings/service.py`** — 384-dim vector embedding service.

**`app/llm/`** — model router + token counter + cost mapping.

**`app/schemas/`** — 14 Pydantic models for API validation.

## 11. API catalog

Every endpoint under `/api/v1/`, grouped by router. Source: `app/api/router.py` + each router file.

### Workflows — `app/api/workflows.py`
- `POST /workflows/submit` · submit plain-English task (for stock agents)
- `POST /workflows` · create w/ typed body
- `GET /workflows` · paginated list
- `GET /workflows/{id}` · detail
- `GET /workflows/{id}/status` · progress + budget
- `GET /workflows/{id}/result` · final synthesized answer
- `GET /workflows/{id}/stream` · SSE progress stream
- `GET /workflows/{id}/reasoning` · reasoning traces
- `POST /workflows/{id}/resume` · cross-node resume primitive
- `POST /workflows/estimate-budget` · rough cost estimate
- `POST /workflows/cleanup-stale` · ops helper
- `DELETE /workflows/{id}` · cancel

### Agents — `app/api/agents.py`
- `GET /agents`, `GET /agents/search`, `POST /agents`, `GET /agents/{id}`, `PATCH /agents/{id}`, `DELETE /agents/{id}`
- `GET /agents/{id}/agent-facts.json` · per-agent signed NANDA doc
- `POST /agents/verify-facts` · verify any AgentFacts document

### Tasks — `app/api/tasks.py`
- `GET /tasks/workflow/{wf_id}`, `GET /tasks/{id}`, `POST /tasks/{id}/override`, `GET /tasks/{id}/actions`
- `GET /tasks/{id}/tests`, `POST /tasks/{id}/tests`, `GET /tasks/{id}/tests/summary`

### Trust — `app/api/trust.py`
- `GET /trust/{agent_id}`, `GET /trust/{agent_id}/history`, `GET /trust/leaderboard/ranked`, `POST /trust/{agent_id}/record`

### Wallet — `app/api/wallet.py`
- `GET /wallet/workflow/{wf_id}/balance`, `GET /wallet/workflow/{wf_id}/events`
- reserve / charge / release / credit per-workflow

### Memory — `app/api/memory.py` + `app/api/memory_sync.py`
- `POST /memory/write`, `POST /memory/query`, `GET /memory/by-hash/{hash}`, `POST /memory/resolve-facts`, `GET /memory/{id}`
- `POST /memory/sync/summary`, `POST /memory/sync/fetch`, `GET /memory/sync/peers`

### Policy — `app/api/policy.py`
- Legacy (JSON-rule): `POST /policy/evaluate`, CRUD under `/policy/rules`
- **Registry (new)**:
  - `GET /policy/registry/rules`
  - `GET /policy/registry/rules/{id}`
  - `POST /policy/registry/rules/{id}/enable`
  - `POST /policy/registry/rules/{id}/disable`
  - `POST /policy/registry/evaluate` · dry-run
- Content safety: `POST /policy/content/analyze`, `POST /policy/injection/analyze`

### Audit — `app/api/audit.py`
- `GET /audit/search`, `GET /audit/workflow/{wf_id}`, `GET /audit/agent/{agent_id}`, `GET /audit/stats`
- `GET /audit/policy-decisions` · signed decision records

### Approvals — `app/api/approvals.py`
- `GET /approvals/pending`, `GET /approvals/{request_id}`
- `POST /approvals/{request_id}/approve`, `POST /approvals/{request_id}/deny`

### Identity — `app/api/identity.py` + `app/api/well_known.py`
- `GET /.well-known/agent-facts.json` · root discovery
- `GET /identity/my-facts`, `POST /identity/verify-facts`, `POST /identity/nanda-lookup`

### Evolution — `app/api/evolution.py`
- `GET /evolution/rankings/leaderboard`, `POST /evolution/promotion`, `POST /evolution/demotion`, `GET /evolution/{agent_id}`

### Tools + MCP — `app/api/tools.py` + `app/api/mcp.py`
- `GET /tools`, `POST /tools/{name}/run`
- `GET /mcp/servers`, `POST /mcp/servers`, `DELETE /mcp/servers/{name}`, `POST /mcp/servers/{name}/reconnect`

### Projects — `app/api/projects.py`
- CRUD + backlog + sprint endpoints (lightweight project management)

### System — `app/api/system.py` + root
- `GET /system/metrics` · Prometheus-compatible metrics
- `GET /health`, `GET /ready` · health + readiness probes
- `GET /api/v1/capabilities` · platform discovery

### Demo — `app/api/demo.py` (gated by `MASSCLAW_DEMO_MODE=true`)
- `POST /demo/seed-workflow`, `POST /demo/self-sign-write` — federation demo primitives

## 12. Frontend catalog

Every route under `frontend/src/app/`.

| Route | Purpose | Key endpoints it hits |
|---|---|---|
| `/` | Dashboard / Mission Control | `/agents`, `/workflows`, `/system/metrics` |
| `/missions` | Paginated workflow list | `/workflows?page=...` |
| `/missions/[id]` | Workflow detail + live SSE stream | `/workflows/{id}`, `/tasks/workflow/{id}`, `/wallet/...` |
| `/agents` | Registry browser | `/agents?page=...` |
| `/agents/[id]` | Agent detail + AgentFacts panel + verify button | `/agents/{id}`, `/agents/{id}/agent-facts.json`, `/trust/{id}`, `/evolution/{id}` |
| `/approvals` | HITL queue — now shows `checkpoint_hash` row per request | `/approvals/pending`, approve / deny |
| `/audit` | Events tab + **Policy Decisions** tab with filter | `/audit/search`, `/audit/policy-decisions` |
| **`/policy`** | Live rule table + toggles + JSON dry-run tester | `/policy/registry/rules`, enable / disable, `/policy/registry/evaluate` |
| `/memory` | Semantic search | `/memory/query` |
| `/wallet` | Budget / reserve history per workflow | `/wallet/workflow/{id}/balance`, `/events` |
| `/tools` | Tool catalog | `/tools` |
| `/tools/playground` | In-browser tool runner | `/tools/code_execute/run` |
| `/trust` | Leaderboard | `/trust/leaderboard/ranked` |
| `/evolution` | Rankings | `/evolution/rankings/leaderboard` |
| `/intelligence` | Reflection aggregation | `/workflows?page_size=10` |
| `/planning` | Projects / backlog / sprint | `/projects`, `/projects/{id}/...` |
| `/mcp` | MCP server catalog | `/mcp/servers` |

### Hooks — `frontend/src/hooks/*.ts`

React Query wrappers: `useAgents`, `useAgent`, `useWorkflows`, `useWorkflow`, `useWorkflowStatus`, `useWorkflowTasks`, `useCreateWorkflow`, `usePendingApprovals`, `useApproveRequest`, `useDenyRequest`, `useWalletBalance`, `useWalletEvents`, `useTools`, `useMCPServers`, `useAgentFacts`, `useInstanceAgentFacts`, `useVerifyAgentFacts`, `useProjects`, `useCreateProject`.

### Shared components worth knowing

- `AgentFactsPanel.tsx` · signed identity doc + verify button
- `agent-plan.tsx` · DAG visualisation with status/reflection/retry badges
- `MissionInput.tsx` · 3-step submit form (prompt + domain + budget)
- `CommandPalette.tsx` · Cmd+K navigation
- `TrustRing.tsx` · circular trust score viz
- `Toast.tsx` · global error/success notifier wired to `ApiError`
- `ErrorBoundary.tsx` · top-level React error catcher

### Theme tokens — `frontend/src/app/globals.css`

```
--mc-bg:           #0C0C0E   --mc-accent:       #E08A3E
--mc-surface:      #161618   --mc-accent-light: #F0A85C
--mc-border:       #252528   --mc-success:      #30D158
--mc-text:         #F5F5F7   --mc-warning:      #FFD60A
--mc-text-muted:   #86868B   --mc-danger:       #FF453A
```

Metallic orange on deep black. Custom classes: `.glass`, `.glass-strong`, `.glow-pulse`, `.animated-border`, `.cpu-architecture`.

### API client — `frontend/src/lib/api.ts`

30 s default timeout via `AbortController`. `api.get|post|patch|delete|getRaw`. Errors raise an `ApiError(status, code, message, nextSteps)` which the Toast listener surfaces — the `next_steps` hint from the server envelope becomes part of the toast text.

## 13. Database models

14 SQLAlchemy models + shared enums in `app/models/base.py`.

| Model | Table | Purpose |
|---|---|---|
| `Agent` | `agents` | Capabilities, trust score, endpoint, protocol type |
| `TrustEvent` | `trust_events` | Per-interaction trust update |
| `AgentScore` | `agent_scores` | Aggregated composite scores |
| `Workflow` | `workflows` | User-submitted task, status, budget, dag_snapshot |
| `Task` | `tasks` | DAG node: capability, retry_count, blocked_reason, output |
| `MemoryRecord` | `memory_records` | CRDT: author_did + parent_hashes + signature + hash + record_state + embedding(384) |
| `PolicyRule` | `policy_rules` | Legacy JSON-rule condition DAG |
| `AuditLog` | `audit_logs` | Event timeline |
| `WalletEvent` | `wallet_events` | Per-workflow ledger with idempotency_key |
| `TaskTest` | `task_tests` | Assertions (regex / schema / LLM-eval) per task |
| `ReasoningTrace` | `reasoning_traces` | Intermediate LLM reasoning |
| `Project` | `projects` | Lightweight PM |
| `BacklogTask` | `backlog_tasks` | Stories on a project |
| `Sprint` + `SprintTask` | `sprints`, `sprint_tasks` | Ordered sprint linkage |

Enums: `AgentStatus`, `WorkflowStatus`, `TaskStatus`, `MemoryType`, `RecordState`, `PolicyAction`, `PolicyRuleType`, `WalletActionType`, `AuditEventType`, `ActorType`.

## 14. Workers + scheduled tasks

`celery -A app.celery_app` with beat schedule:

| Task | Cadence (demo / prod) | What it does |
|---|---|---|
| `health_check_all` | 60 s | Poll `agent.health_check_url`, update `last_health_check` + `status` |
| `decay_trust_scores` | 24 h | Decay agents with no recent tasks |
| `garbage_collect_memory` | 6 h | Tombstone expired records, hard-delete old tombstones (10 000-row batch cap) |
| `recalculate_scores` | 12 h | Roll up AgentScore, run evolution promotion/demotion |
| `crdt_gossip_tick` | 5 s / 30 s | Gossip sync to 2 random peers |

Broker + backend: Redis. Serialiser: JSON.

## 15. Middleware + error envelope

Stack (outermost first, registered in `app/main.py`):

```
ErrorHandlerMiddleware    ← catches uncaught exceptions
BodySizeLimitMiddleware   ← 1 MiB cap, returns 413 envelope
RateLimitMiddleware       ← sliding window 100 / 60 s default
RequestLoggingMiddleware
CorrelationIdMiddleware   ← X-Correlation-ID header
CORSMiddleware
```

Plus FastAPI-level exception handlers for `StarletteHTTPException` and `RequestValidationError` so every error response is a canonical envelope.

### Envelope shape — `app/core/errors.py`

```json
{
  "error_code": "VALIDATION_ERROR",
  "detail":     "Request validation failed",
  "next_steps": "Inspect the `errors` field to fix the malformed input, then retry.",
  "correlation_id": "uuid",
  "errors":     [{"loc": ["body", 1], "msg": "…", "type": "json_invalid"}],
  "extra":      {"limit_bytes": 1048576}
}
```

`NEXT_STEPS_HINTS` maps every canonical `error_code` to an actionable hint. Stock agents see structured guidance, never bare strings or stack traces.

## 16. Federation compose

`docker/federation.yml` — 12 containers:

```
node-a: postgres-a, redis-a, backend-a, worker-a   → :18001
node-b: postgres-b, redis-b, backend-b, worker-b   → :18002
node-c: postgres-c, redis-c, backend-c, worker-c   → :18003
```

Each backend gets:

- `MASSCLAW_DEMO_MODE=true` · enables `/demo/*` endpoints
- `GOSSIP_INTERVAL_SECONDS=5` · fast demos
- `MASSCLAW_FEDERATION_PEERS` · other two nodes
- `CORS_ORIGINS` · allows 18001–18003 + 3000–3002
- Ephemeral keypair in tmpfs so every demo is a fresh run

Ops scripts — `scripts/`:

| Script | What it does |
|---|---|
| `demo_up.sh` | Boot all 12 containers |
| `demo_down.sh` | Tear down |
| `demo_write.sh node-a "…"` | Signed memory write |
| `demo_partition.sh node-c` | `docker network disconnect` — real partition |
| `demo_heal.sh node-c` | Reconnect + wait for health |
| `demo_summary.sh` | Show all three Merkle roots |
| `demo_query.sh node-a "?" --mode audit` | Three-mode fact resolve |
| `demo_federation_test.sh` | All scenarios in one script — `exit 0` only on convergence |
| `chaos_partition_mid_write.sh` | Partition while a write is still propagating |
| `chaos_kill_mid_write.sh` | Hard-kill a node right after a write |
| `chaos_malformed_records.sh` | Hammer bad input, assert every error is an envelope |

## 17. Testing strategy

### 17.1 Automated — 616 tests

```bash
cd backend
python -m pytest tests/unit tests/integration \
  --ignore=tests/unit/test_scheduler_cache.py -q
# expect: 616 passed in ~12 s
```

**Eleven files covering the new systems (139 tests):**

| File | Focus |
|---|---|
| `tests/unit/test_orchestration_checkpoint.py` | `WorkflowCheckpoint` serialisation, metadata marker, DAG restore |
| `tests/unit/test_approval_checkpoint_hash.py` | `ApprovalRequest.checkpoint_hash` roundtrip |
| `tests/unit/test_reflection_cache.py` | Cache hit/miss/TTL, broken-Redis resilience |
| `tests/unit/test_policy_decision.py` | `Decision` constructors + `aggregate()` precedence |
| `tests/unit/test_policy_registry.py` | Decorator registration, toggle, evaluate with filters |
| `tests/unit/test_policy_builtin_rules.py` | Every branch of each of the 10 rules |
| `tests/integration/test_orchestration_resume.py` | Cross-node resume, idempotency, unknown-hash |
| `tests/integration/test_scheduler_reflection_branches.py` | Accept/retry/re_plan/abort observable side effects |
| `tests/integration/test_policy_registry_api.py` | HTTP surface of the registry endpoints |
| `tests/integration/test_policy_audit.py` | Signed decision persistence + `GET /audit/policy-decisions` |
| `tests/integration/test_edge_cases.py` | Malformed JSON, invalid UUID, oversized, unicode, 404/405/413/429, concurrent checkpoint dedup, rate-limit envelope, resume with unknown hash |

Plus the older layer tests (identity, CRDT, memory lifecycle, trust, wallet, tools, MCP, protocols, DAG, scheduler cache) — total 616.

### 17.2 Manual — curl smoke

See [§18](#18-section-by-section-test-plan).

### 17.3 Live federation — Docker

See [§16](#16-federation-compose) + the `demo_*.sh` and `chaos_*.sh` scripts.

### 17.4 UI smoke

See [§18.3](#183-frontend-click-through).

## 18. Section-by-section test plan

Run top-to-bottom. Each section is self-contained.

### 18.1 Boot the stack

```bash
cd backend
alembic upgrade head

AUTH_REQUIRED=false \
  IDENTITY_KEY_ENCRYPTION_KEY=$(printf '33%.0s' {1..32}) \
  uvicorn app.main:app --port 8000 &

cd ../frontend
npm run dev -- --port 3000
```

Optional for gossip: `celery -A app.celery_app worker --loglevel=INFO &` + `celery -A app.celery_app beat --loglevel=INFO &`.

### 18.2 Backend API smoke — 20 scenarios

```bash
B=http://localhost:8000

# ── Policy engine ─────────────────────────────
# 1. List all 10 rules
curl -s $B/api/v1/policy/registry/rules | jq 'length'                          # 10

# 2. Low-trust payment → deny
curl -s -X POST $B/api/v1/policy/registry/evaluate \
  -H 'Content-Type: application/json' \
  -d '{"agent_trust_score":0.2,"action_category":"payment","amount":100}' \
  | jq '.action, .rule_id'                                                     # "deny" / "trust_floor_for_payments"

# 3. Mid-trust payment → escalate_human
curl -s -X POST $B/api/v1/policy/registry/evaluate \
  -H 'Content-Type: application/json' \
  -d '{"agent_trust_score":0.5,"action_category":"payment","amount":100}' \
  | jq '.action'                                                               # "escalate_human"

# 4. Disable rule → re-run #2 → different rule takes over (proves toggle)
curl -s -X POST $B/api/v1/policy/registry/rules/trust_floor_for_payments/disable
curl -s -X POST $B/api/v1/policy/registry/evaluate \
  -H 'Content-Type: application/json' \
  -d '{"agent_trust_score":0.2,"action_category":"payment","amount":100}' \
  | jq '.action, .rule_id'                                                     # "escalate_human" / "time_of_day_restrictions"
curl -s -X POST $B/api/v1/policy/registry/rules/trust_floor_for_payments/enable

# 5. PII tool → escalate_human regardless of trust
curl -s -X POST $B/api/v1/policy/registry/evaluate \
  -H 'Content-Type: application/json' \
  -d '{"tool_name":"lookup_ssn","agent_trust_score":0.95}' \
  | jq '.action'                                                               # "escalate_human"

# 6. Cost cap breach → deny
curl -s -X POST $B/api/v1/policy/registry/evaluate \
  -H 'Content-Type: application/json' \
  -d '{"estimated_cost":500,"action":"execute_tool"}' \
  | jq '.action'                                                               # "deny"

# ── Workflow lifecycle ────────────────────────
# 7. Submit
WF=$(curl -s -X POST $B/api/v1/workflows/submit \
  -H 'Content-Type: application/json' \
  -d '{"instruction":"Summarize three supply chain bottlenecks","budget":25}' \
  | jq -r '.workflow_id')

# 8. Status
curl -s $B/api/v1/workflows/$WF/status | jq

# 9. Stream (SSE) — open in another terminal
curl -N $B/api/v1/workflows/$WF/stream | head -n 20

# 10. Result
curl -s $B/api/v1/workflows/$WF/result | jq

# ── HITL ──────────────────────────────────────
# 11. Pending approvals
curl -s $B/api/v1/approvals/pending | jq 'length'

# 12. Approve a specific request (simulate: POST-approve / POST-deny with dummy id → 404 envelope)
curl -s -X POST $B/api/v1/approvals/fake-id/approve \
  -H 'Content-Type: application/json' \
  -d '{"reason":"ok","decided_by":"human"}' | jq

# 13. Resume endpoint with unknown hash → 404 envelope
curl -s -X POST $B/api/v1/workflows/00000000-0000-0000-0000-000000000000/resume \
  -H 'Content-Type: application/json' \
  -d '{"checkpoint_hash":"zMissing"}' | jq

# ── Memory ────────────────────────────────────
# 14. Semantic query
curl -s -X POST $B/api/v1/memory/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"supply chain","top_k":5}' | jq 'length'

# 15. Get by hash (use a valid one from step 16)
# curl -s $B/api/v1/memory/by-hash/{hash} | jq

# ── Audit ─────────────────────────────────────
# 16. Policy decisions (newest first)
curl -s "$B/api/v1/audit/policy-decisions?limit=10" | jq 'length'

# 17. Filter by action
curl -s "$B/api/v1/audit/policy-decisions?action=deny&limit=20" | jq 'length'

# 18. Bad filter → 422
curl -s "$B/api/v1/audit/policy-decisions?action=shrug" | jq .error_code       # "VALIDATION_ERROR"

# ── Well-known ────────────────────────────────
# 19. NANDA discovery
curl -s $B/.well-known/agent-facts.json | jq '.label, .skills | length'

# 20. Verify a facts document
curl -s $B/.well-known/agent-facts.json > /tmp/facts.json
curl -s -X POST $B/api/v1/agents/verify-facts \
  -H 'Content-Type: application/json' -d @/tmp/facts.json | jq
```

### 18.3 Frontend click-through

Visit http://localhost:3000 and walk this script in order. Open DevTools → Network to confirm calls succeed.

| # | Route | Action | Expected |
|---|---|---|---|
| 1 | `/` | Load | Stats fill (agents, workflows, memory). No `—` placeholders |
| 2 | `/agents` | Load + click "Next" page + click an agent | 200 on `/agents?page=2`, 200 on `/agents/{id}` |
| 3 | `/agents/[id]` | Scroll to AgentFacts panel → click "Verify" | Green check appears |
| 4 | `/missions` | Click a recent mission | Detail loads |
| 5 | `/missions/[id]` | Expand a task | Reflection badge visible |
| 6 | `/missions/new` (or the input on `/`) | Submit a task | Redirect to detail; SSE events render |
| 7 | `/policy` | Count rules | Ten rows |
| 8 | `/policy` | Click a toggle | Row colour flips; Network shows `POST .../disable` 200 |
| 9 | `/policy` | Paste JSON in tester + click **evaluate** | Decision badge renders with action + rule_id + contributing rules |
| 10 | `/audit` | Click **Policy Decisions** tab | Table of signed decisions renders |
| 11 | `/audit` | Filter by `deny` | Rows filter in place |
| 12 | `/audit` | Click a row | Expanded view shows decision + context + signing DID |
| 13 | `/approvals` | Load | Either `All clear` empty state OR cards with **Resume Pointer (checkpoint hash)** row |
| 14 | `/approvals` | Click a hash | Full hash copies to clipboard |
| 15 | `/memory` | Semantic search | Ranked results |
| 16 | `/wallet` | Expand a workflow row | Transaction history |
| 17 | `/tools` + `/tools/playground` | Run a Python snippet | Output + exit code |
| 18 | `/trust`, `/evolution`, `/intelligence` | Load | Real data, no blank screens |
| 19 | `/mcp` | Load | Server catalog (empty is fine) |
| 20 | `/planning` | Create a project | Appears in list |

### 18.4 Federation (needs Docker)

```bash
./scripts/demo_up.sh
./scripts/demo_federation_test.sh      # scripted end-to-end
./scripts/chaos_partition_mid_write.sh # stress
./scripts/demo_down.sh
```

### 18.5 Chaos

Each script asserts structured exit code — use in CI:

- `chaos_partition_mid_write.sh` → converges after heal OR fails loud
- `chaos_kill_mid_write.sh` → peers hold the record if gossip fired
- `chaos_malformed_records.sh` → every error is an envelope

## 19. Edge case catalog

Run these deliberately to surface real failures. Grouped by system.

### Input / schema
1. Empty workflow prompt → 422 (Pydantic `min_length`)
2. Prompt > 50 KB → 422
3. Invalid UUID path param → 422 with `errors[].loc`
4. Bad JSON body → 422 · `VALIDATION_ERROR`
5. Body > 1 MiB → 413 · `PAYLOAD_TOO_LARGE`
6. Unknown route → 404 envelope (not default FastAPI text)
7. Wrong HTTP method → 405 envelope
8. Unicode / emoji / RTL in prompt → 200, round-trips
9. Very long `request_id` (>128 chars) → 422 (capped)
10. Unknown `?action=` in audit filter → 422

### Policy engine
11. Disabled rule stops firing → confirm another rule takes over
12. Two denies → aggregate picks first but lists both in `contributing_rule_ids`
13. Crashing rule → counted as abstain, other rules still run
14. Non-Decision return from a rule → abstain, logged
15. Priority tie-break → deterministic (rule_id ASC)
16. Aggregation with no rules registered → abstain
17. Dry-run with `rule_ids=[x]` bypasses registry state

### Memory / CRDT
18. Duplicate checkpoint save → same hash, no second row (SAVEPOINT dedup)
19. Racing writer at the SQL unique-index level → existing row wins
20. Content hash ≠ canonical-body hash → `SignatureMismatchError`
21. Record write without `author_did` when `memory_write_attribution` enabled → deny
22. Parent hash cycle → either rejected at write or only first parent seen

### HITL / resume
23. Resume with unknown `checkpoint_hash` → 404 envelope (KeyStore fallback required)
24. Resume with mismatched `workflow_id` → 409
25. Resume with an approval that was `denied` → 409
26. Double-resume same checkpoint → idempotent; same rows
27. Approve same `request_id` twice → second call 409 "already approved"
28. Expired approval (TTL ≤ 0) → `status=expired` in `wait_for_decision`
29. Resume on a peer that doesn't have the Workflow row yet → creates shadow Workflow

### Reflection / retry / replan
30. Same output hash twice → reflection cache hit, no LLM call
31. Retry at `retry_count == MAX_RETRIES_PER_TASK` → `SelfCorrectionEngine` refuses
32. `re_plan` on a node with no parents → `DynamicReplanner` adds `{id}_alt`
33. `abort` when CheckpointStore save fails (e.g. no KEK) → logged; workflow still marked failed (resilient)

### Approvals UI
34. Approval list empty → "All clear" card renders (not a blank page)
35. Approval with null `checkpoint_hash` → row hidden, no "undefined" in the hash slot

### Rate limit / error envelope
36. Burst > 100 in 60 s → 429 envelope with `Retry-After` header
37. Redis down during rate-limit check → fail-open or 503 (configurable)
38. Any 5xx → envelope with `correlation_id`, no stack trace leaked

### Federation
39. Partition mid-write → write survives on the writer, gossips after heal
40. Kill mid-write → peers that already pulled have it; others don't (expected timing behaviour)
41. Gossip tick against a peer that's 404 → logged, next tick tries another peer
42. Merkle bucket mismatch → pull just that bucket (not the whole set)

### Identity
43. Corrupt instance key (not 32 bytes) → `KeyStoreError` at startup
44. Rotate instance key → AgentFacts must be re-published (DID changes)
45. `did:web` with no HTTP reachability → caller pre-verifies externally
46. Sign with a public key that doesn't match the private seed → `SignatureMismatchError`

### Frontend
47. API timeout > 30 s → `ApiError("TIMEOUT")`, toast "Request timed out after 30000 ms"
48. `next_steps` in envelope → appended to toast text
49. `refetchInterval` during component unmount → canceled via React Query
50. Theme toggle dark/light → no visible flicker (CSS vars swap in one frame)

## 20. How to run the whole thing

```bash
# First time
cd backend
python -m pip install -r requirements.txt
python -m pip install -e .
alembic upgrade head

cd ../frontend
npm install

# Backend
cd backend
AUTH_REQUIRED=false \
  IDENTITY_KEY_ENCRYPTION_KEY=$(printf '33%.0s' {1..32}) \
  uvicorn app.main:app --port 8000

# Frontend (new terminal)
cd frontend
npm run dev

# Celery for gossip + GC + trust decay (optional; new terminal)
cd backend
celery -A app.celery_app worker --loglevel=INFO
celery -A app.celery_app beat --loglevel=INFO

# Tests
cd backend
python -m pytest tests/unit tests/integration \
  --ignore=tests/unit/test_scheduler_cache.py -q

# Federation (Docker)
./scripts/demo_up.sh
./scripts/demo_federation_test.sh
./scripts/demo_down.sh

# Open the live status board
open file:///Users/deepakzedler/Documents/MassClaw/massclaw_dashboard.html
```

## 21. Open questions + future work

- **Live stock-agent harness (Day 19)**: wire an unmodified Claude / GPT loop against only the MassClaw URL; run the five hackathon scenarios end-to-end.
- **L2 receipt anchoring**: optional blockchain anchor for extra-public provenance (Phase 2).
- **Learning-from-reflection pipeline**: feed reflection verdicts back into the planner for long-horizon improvement.
- **OPA/Rego backend for policy**: the `@policy_rule` registry already isolates the decision surface, so swapping the backend is a Phase-2 line item.
- **ZERIX robustness credentials**: `credential_requires` already has the opt-in hook. ZERIX ships credentials; MassClaw verifies.
- **10-node federation + real-world latency**: Fly.io × 3 regions is configured but not yet under real load.

---

### Related docs

- `docs/end_to_end_flow.md` — shorter plain-language walk-through
- `docs/testing_guide.md` — copy-paste smoke tests
- `docs/phase1_checklist.md` — done / pending tracker
- `docs/federation.md` — federation runbook (judge-oriented)
- **Live status** — `massclaw_dashboard.html` (root + `docs/dashboard.html`)
