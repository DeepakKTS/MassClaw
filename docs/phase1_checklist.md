# Phase-1 Submission Checklist

Everything below must be "done" before the Phase-1 deadline (2026-05-07). Tracks the full 21-day build.

---

## Backend — identity layer

- [x] Ed25519 keypair + encrypted-at-rest KeyStore (`backend/app/identity/key_store.py`)
- [x] Canonical JSON-LD AgentFacts v1 builder + signer + verifier (`backend/app/identity/agent_facts.py`)
- [x] `did:key`, `did:web`, `did:nanda` resolvers (`backend/app/identity/did.py`)
- [x] `/.well-known/agent-facts.json` (`backend/app/api/well_known.py`)
- [x] `/agents/{id}/agent-facts.json` — per-agent signed facts
- [x] NANDA Index client + auto-register on startup (`backend/app/services/nanda_index.py`)

## Backend — CRDT memory layer

- [x] `MemoryRecord` model with `author_did`, `parent_hashes`, `signature`, `content_hash` (`backend/app/models/memory.py`)
- [x] Alembic migration for the new fields
- [x] Content-addressed hashing + multibase signature (`backend/app/crdt/hashing.py`, `backend/app/identity/signer.py`)
- [x] `CRDTStore.put` with signature verify, idempotent duplicate-hash inserts (SAVEPOINT)
- [x] Three-mode fact resolver — planning / audit / sensitive with HITL (`backend/app/services/memory_service.py`)
- [x] Merkle 256-bucket sync (`backend/app/crdt/merkle.py`)
- [x] Peer-authenticated gossip endpoints (`/api/v1/memory/sync/summary`, `/api/v1/memory/sync/fetch`)
- [x] Background Celery gossip worker (`backend/app/workers/crdt_gossip.py`)
- [x] Lifecycle states (ACTIVE → SUPERSEDED → HISTORICAL → TOMBSTONED) + bounded GC

## Backend — orchestration + adaptive intelligence

- [x] `WorkflowScheduler.execute_workflow` with DAG-aware parallel execution
- [x] `WorkflowCheckpoint` + `CheckpointStore` — signed META memory record
- [x] Reflection engine with Redis-backed result cache (`backend/app/intelligence/reflection.py`)
- [x] `SelfCorrectionEngine` + `DynamicReplanner` wired into the scheduler's four-branch dispatch (`accept` / `retry_task` / `re_plan` / `abort`)
- [x] `WorkflowResumer` + `POST /api/v1/workflows/{id}/resume` — cross-node resume primitive

## Backend — HITL + safety

- [x] Redis-backed `ApprovalManager` with `checkpoint_hash` field
- [x] Approval gate in the scheduler — saves signed checkpoint before pausing
- [x] `POST /api/v1/approvals/{id}/approve|deny` with `next_steps` envelope
- [x] Policy registry (`@policy_rule` decorator + `PolicyRegistry`)
- [x] `PolicyContext`, `Decision`, `aggregate()` with deny > escalate > allow > abstain precedence
- [x] Ten built-in rules: trust floor for payments, rate limit per tool, cost caps, time-of-day, PII guards, memory-write attribution, cross-agent delegation, credential-requires, signature-requires, default-deny-unknown-tool
- [x] `load_builtin_rules()` called on app startup
- [x] `/api/v1/policy/registry/*` API — list, toggle, dry-run evaluate
- [x] Signed policy-decision audit records (`backend/app/safety/audit.py`) + `GET /api/v1/audit/policy-decisions`

## Backend — hardening

- [x] Standard `ErrorEnvelope` with `error_code`, `detail`, `next_steps`, `correlation_id`
- [x] FastAPI exception handlers for `StarletteHTTPException` + `RequestValidationError`
- [x] Dict-detail preservation so `raise HTTPException(detail={...})` round-trips
- [x] Body-size cap middleware (`MASSCLAW_MAX_REQUEST_BODY_BYTES`, default 1 MiB)
- [x] Rate-limit middleware with fail-open option + structured 429 envelope
- [x] All unbounded DB GC loops capped with `LIMIT 10_000` per tick
- [x] Broad `except Exception` in approval endpoints replaced with specific error mapping
- [x] Pydantic bounds on every new request body (min/max length, ge/le, gt=0 on budgets)
- [x] Scheduler tool-loop exhaustion forces `tools=None` summary call (Day 22)
- [x] Task rows upserted by `node.task_id` — no duplicate rows on outer-loop re-entry (Day 22)
- [x] `reserve_budget` carries per-node idempotency key (Day 22)
- [x] Low-confidence-review approval fires once per task attempt (Day 22)
- [x] `memory_records.workflow_id` nullable so cross-workflow semantic cache works (Day 22)
- [x] Wallet balance endpoint reconciles ledger with `Workflow.budget_used` (Day 22)
- [x] `KeyStore` fallback to `~/.massclaw` when `/var/lib/massclaw` not writable (Day 22)

## Backend — tests

- [x] 622 unit + integration tests passing
- [x] `test_orchestration_checkpoint.py` — 14 tests on the serialisation surface
- [x] `test_orchestration_resume.py` — 5 tests on cross-node resume
- [x] `test_scheduler_reflection_branches.py` — 6 tests, one per reflection action
- [x] `test_reflection_cache.py` — 10 tests on cache hit/miss/TTL/broken-Redis
- [x] `test_policy_decision.py` + `test_policy_registry.py` + `test_policy_builtin_rules.py` — 70 policy tests
- [x] `test_policy_registry_api.py` + `test_policy_audit.py` — 16 API-level tests
- [x] `test_edge_cases.py` — 14 hardening tests (malformed JSON, invalid UUID, oversized, unicode, 404, 405, 413, 429 envelope)
- [x] `test_scheduler_tool_loop.py` — 6 tests (tool-loop exhaustion, Task upsert, reserve idempotency, review flag reset)
- [x] E2E federation convergence test behind `@pytest.mark.e2e`

## Frontend

- [x] `/policy` — live rule table with toggle-in-place + JSON dry-run tester
- [x] `/audit` — tabbed view (Events + Policy Decisions) with expand-for-context + filters
- [x] `/agents` — error + empty states + pagination
- [x] `/missions`, `/missions/[id]` — SSE-driven progress + reflection verdicts
- [x] `/approvals` — checkpoint-hash column + approve/deny
- [x] `/memory` — semantic search
- [x] `/trust`, `/wallet`, `/evolution`, `/tools`, `/mcp`, `/planning`, `/intelligence` — layer views
- [x] `api.ts` — 30 s default timeout via AbortController, structured error toast with `next_steps`
- [x] Next.js production build clean
- [x] `/../../system/metrics` path bug fixed (TopBar, QuickStats, DashboardStats)

## Federation + deployment

- [x] `docker/federation.yml` — 3 × {postgres, redis, backend, worker, beat} = 12 containers
- [x] Demo scripts: `demo_up.sh`, `demo_down.sh`, `demo_write.sh`, `demo_partition.sh`, `demo_heal.sh`, `demo_summary.sh`, `demo_query.sh`, `demo_federation_test.sh`
- [x] Chaos scripts: `chaos_partition_mid_write.sh`, `chaos_kill_mid_write.sh`, `chaos_malformed_records.sh`
- [x] Fly.io × 3 regions configured under `deploy/fly/`
- [x] Cloudflare Tunnel fallback documented
- [x] CORS allowlist includes all three federation ports + three frontend ports

## Docs

- [x] `README.md` — landing page + quickstart
- [x] `docs/federation.md` — judge runbook for the 3-node demo
- [x] `docs/end_to_end_flow.md` — this build's flow, plain-language
- [x] `docs/testing_guide.md` — copy-paste test script
- [x] `docs/phase1_checklist.md` — this document
- [x] `CHANGELOG.md` reflects Day-12 → Day-18 work

## Still pending (Days 19–21)

- [ ] **Day 19** — Stock-agent black-box test against a real Claude/GPT loop, five scenarios
- [ ] **Day 20** — Screen-record five demo clips + friend-as-judge dry run
- [ ] **Day 21** — Final submission form + checklist sign-off

## Success criteria (from the Phase-1 plan)

1. ✅ Live public MassClaw deployment discoverable via well-known URL (local-first, Cloudflare Tunnel ready)
2. ✅ Three-node federation demonstrable via `docker/federation.yml`, converging after partition within 10 s
3. ⏳ Stock Claude/GPT agent completes all five black-box scenarios without MassClaw-specific prompt tuning *(Day 19)*
4. ✅ Policy engine extensible live — a judge can write and save a new rule in 30 s and see it fire
5. ✅ Cross-node workflow resume demonstrable (kill executor, approve + resume on peer)
6. ✅ Signed-receipt audit trail queryable end-to-end
7. ✅ Judge-facing README loads value in the first scroll
8. ⏳ Five demos recorded as backup *(Day 20)*
