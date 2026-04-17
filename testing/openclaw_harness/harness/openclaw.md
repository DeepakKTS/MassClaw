# MassClaw for OpenClaw Agents

**Base URL:** `http://localhost:18001` (node A). Nodes `http://localhost:18002` and `http://localhost:18003` are identical peers — pick any one; records you write propagate to all three within ~10 seconds.

**What MassClaw does.** MassClaw is a decentralized operating layer for AI agents. You hand it a task in plain English, it decomposes the task into steps, picks the right tools (web search, code execution, file I/O, API calls), executes them, and returns a synthesized result — with cryptographic receipts, shared memory, and human-in-the-loop approval when the task is risky.

**What you, an OpenClaw agent, need to know.** You interact with MassClaw entirely over HTTP. No auth is required in local/dev mode. Every endpoint returns JSON. Every error response follows the same shape:

```json
{
  "error_code": "VALIDATION_ERROR",
  "detail": "human-readable explanation",
  "next_steps": "what to do next",
  "correlation_id": "..."
}
```

**When you get an error, always read `next_steps` and follow its guidance.** That field exists specifically for agents like you.

---

## Discovering what MassClaw can do

Before doing anything else, fetch:

```
GET http://localhost:18001/.well-known/agent-facts.json
```

This returns a signed [NANDA AgentFacts v1](https://github.com/projnanda/agentfacts-format) document with:

- `agent_name`, `label`, `description` — what this instance is
- `endpoints.static[]` — every HTTP URL you can call
- `skills[]` — structured list of capabilities (id, description, inputModes, outputModes, latencyBudgetMs)
- `capabilities.modalities` — input/output types this instance supports
- `verifiable_credentials[]` — Ed25519 signature proving the document is genuine

The response's `skills` array is your map of what MassClaw offers. Use it.

To cryptographically verify the AgentFacts document:

```
POST http://localhost:18001/api/v1/agents/verify-facts
Content-Type: application/json

<the full AgentFacts JSON you just fetched>
```

Response `{ "verified": true }` means the signature is valid and the document is genuine.

To resolve a DID (decentralized identifier) to an AgentFacts document:

```
GET http://localhost:18001/api/v1/identity/resolve?did=<did>
```

Supports `did:key:...`, `did:web:...`, `did:nanda:...`. Optional `&username_hint=<name>` for NANDA Index lookups.

---

## Submitting a task (the core flow)

Submit a task in plain English:

```
POST http://localhost:18001/api/v1/workflows/submit
Content-Type: application/json

{
  "instruction": "Sort the list [3, 1, 4, 1, 5, 9, 2, 6] in ascending order",
  "budget": 500,
  "domain": "general",
  "priority": "normal"
}
```

Fields:
- `instruction` (string, required) — what you want done, in plain English
- `budget` (integer, optional, default 100) — max credits MassClaw is allowed to spend; reject the task rather than exceed this
- `domain` (string, optional, default "general") — hints at the domain so MassClaw picks the right agents
- `priority` (integer, optional, default `5`) — priority level 1–10 where 1 is highest

Response:

```json
{
  "workflow_id": "...",
  "status": "pending",
  "track_url": "/api/v1/workflows/<id>/status",
  "result_url": "/api/v1/workflows/<id>/result"
}
```

Poll status until terminal:

```
GET http://localhost:18001/api/v1/workflows/<id>/status
```

Valid `status` values (returned **lowercase**, exactly as shown):
- `pending` — queued
- `decomposing` — MassClaw is breaking the task down
- `running` — executing steps
- `awaiting_approval` — a human reviewer must approve before continuing (see HITL below)
- `completed` — done; fetch the result
- `failed` — execution error; the status payload has details
- `cancelled` — user-terminated

Poll every 1–3 seconds. Stop polling when status ∈ {`completed`, `failed`, `cancelled`}. Use case-insensitive comparisons if in doubt — the API returns lowercase strings.

When status is `COMPLETED`, fetch the result:

```
GET http://localhost:18001/api/v1/workflows/<id>/result
```

Response contains the synthesized answer, the DAG of steps MassClaw executed, and per-step traces.

---

## Tasks that use tools

MassClaw has seven built-in tools any workflow can use:

- `web_search` — search the web (Brave). Input: query string.
- `web_scrape` — fetch a URL and extract text.
- `code_execute` — run Python/JavaScript/Shell in a sandbox.
- `file_read`, `file_write`, `file_list` — workspace file I/O.
- `api_call` — make arbitrary HTTP requests to external APIs.

You do NOT call these tools directly — you submit a workflow whose `instruction` implies their use, and MassClaw's planner decides which to invoke. E.g., `instruction: "Search the web for 'MIT Media Lab' and tell me what they do"` triggers `web_search`.

To see the full tool registry with parameter schemas:

```
GET http://localhost:18001/api/v1/tools
```

---

## Querying shared memory

MassClaw keeps a decentralized shared memory of facts (CRDT-synced across the three nodes). To search it semantically:

```
POST http://localhost:18001/api/v1/memory/query
Content-Type: application/json

{
  "query": "what is the deadline",
  "top_k": 5,
  "min_similarity": 0.5,
  "min_confidence": 0.0
}
```

Response is a list of ranked records, each with `memory`, `similarity` (cosine), and `relevance_score` (combined similarity + freshness + confidence).

To retrieve a specific record by its content hash:

```
GET http://localhost:18001/api/v1/memory/by-hash/<hash>
```

---

## Conflicting facts — three modes

When memory contains conflicting facts about the same subject (e.g., two different deadlines), use `POST /api/v1/memory/facts/resolve` to choose how to handle the conflict:

```
POST http://localhost:18001/api/v1/memory/facts/resolve
Content-Type: application/json

{
  "subject": "deadline",
  "mode": "planning",
  "top_k": 10
}
```

Modes:
- `planning` — you get ONE chosen winner (highest-ranked candidate). Best for "what should I do next?" contexts.
- `audit` — you get ALL candidates ranked. Best for "who said what, when?" explanations.
- `sensitive` — if there's a real conflict, MassClaw escalates to a human (`requires_hitl: true`) instead of answering. Use for decisions with consequences.

Response always contains `mode`, `subject`, `chosen` (null when HITL), `candidates[]`, `requires_hitl`, and a `reason` string.

---

## Federation — the three-node network

Nodes 18001, 18002, 18003 are peers. They run a gossip protocol every 5 seconds and converge on the same set of signed records within ~10 seconds.

To see each node's current Merkle summary (a 256-bucket fingerprint of its memory):

```
GET http://localhost:18001/api/v1/memory/sync/summary
```

**Two ways to call this, depending on who you are:**

- **As a client (e.g., a stock OpenClaw agent or a judge checking convergence)**: when the node runs with `MASSCLAW_DEMO_MODE=true` (true in all local and demo deployments), send no auth headers at all — the endpoint accepts the request as a read-only convergence probe. Poll each of the three ports (18001, 18002, 18003) and compare the `root` fields — if all three match, the network is converged. Only the `summary` endpoint is anonymous; `buckets` and `fetch` always require peer signing.

- **As a peer node (exchanging gossip, fetching records)**: generate an Ed25519 keypair, sign `<unix-ts>|GET|/api/v1/memory/sync/summary`, and send `X-Peer-DID`, `X-Peer-Timestamp`, `X-Peer-Signature` headers. Required for `buckets/{i}` and the POST `fetch` endpoint — those expose record content.

To test the federation lives up to its promise, use the demo endpoints (only available when `MASSCLAW_DEMO_MODE=true`):

- `POST /api/v1/demo/seed-workflow` — create a workflow row with a caller-supplied UUID so you can write signed records into it.
- `POST /api/v1/demo/self-sign-write` — ask the node to sign a record with its own instance key and persist it. Returns the record's `hash` and `author_did`.

After calling `self-sign-write` on node A, wait ~10s, then `GET /memory/by-hash/<hash>` on node B and node C — the record should be present on both.

---

## Human-in-the-loop (HITL) approvals

Some tasks are high-risk (spending money, destructive file ops, public communication). MassClaw pauses those workflows before execution and waits for a human to approve.

When you submit such a workflow, its status transitions through `pending` → `decomposing` → `awaiting_approval`. When you see that state:

1. Find the pending approval request:
   ```
   GET http://localhost:18001/api/v1/approvals/pending
   ```
   Each pending item has fields including `request_id`, `checkpoint_hash` (important — keep it), `policy_rule`, and `action`.

2. Approve it (or deny it):
   ```
   POST http://localhost:18001/api/v1/approvals/<request_id>/approve
   Content-Type: application/json

   {"reason": "approved for test", "decided_by": "agent-operator"}
   ```
   Response includes `approval_id` and `status: approved`.

3. Resume the workflow (both `checkpoint_hash` and `approval_id` are required):
   ```
   POST http://localhost:18001/api/v1/workflows/<workflow_id>/resume
   Content-Type: application/json

   {
     "checkpoint_hash": "<the checkpoint_hash from step 1>",
     "approval_id": "<the approval_id from step 2>"
   }
   ```
   The checkpoint hash pins the resume to a specific paused state so an approval can't be replayed against a different checkpoint.

4. Resume takes a few seconds. Poll `/status` until `completed`.

To deny instead of approve, use `POST /api/v1/approvals/<request_id>/deny` with the same body shape as `/approve`.

---

## Wallet and budget

Every workflow has a wallet. When you submit with `{"budget": 500}`, MassClaw creates a wallet with a 500-credit cap. Each agent that does work for the workflow reserves budget (atomic reservation, preventing overspend), then charges the actual cost after completion (and any reserved-but-not-spent amount is released).

To see the current balance for a workflow:

```
GET http://localhost:18001/api/v1/wallet/workflow/<workflow_id>/balance
```

To see the full ledger of wallet events (reserves, charges, releases, credits):

```
GET http://localhost:18001/api/v1/wallet/workflow/<workflow_id>/events
```

Each event has `action_type` ∈ `{CREDIT, DEBIT, RESERVE, RELEASE}`, `amount`, `agent_id`, `created_at`, and `reason`.

If an agent attempts to reserve more than the remaining balance, MassClaw returns `402 Payment Required` with `error_code: BUDGET_EXHAUSTED` and `next_steps` explaining how to raise the cap or split the work.

---

## Recovering from errors

Every error follows the envelope above. The most common `error_code` values and what to do:

- `VALIDATION_ERROR` (422) — your request body didn't match the schema. The `errors[]` array tells you which fields. Fix and retry.
- `NOT_FOUND` (404) — the resource doesn't exist (wrong workflow_id, wrong approval_id). Re-fetch the list.
- `CONFLICT` (409) — you're trying to operate on a resource in the wrong state (e.g., approving an already-decided request). Re-fetch and decide.
- `BUDGET_EXHAUSTED` (402) — see Wallet section. Raise the cap or narrow the task.
- `AGENT_UNAVAILABLE` (503) — no qualified agent could take the job. Retry after 5s, then widen the domain hint.
- `POLICY_VIOLATION` (403) — the policy engine blocked the action. `next_steps` explains which rule.
- `INTERNAL_ERROR` (500) — MassClaw bug. Log the `correlation_id` and retry once; if it persists, stop and report.

---

## OpenAPI

For the full machine-readable contract, fetch:

- `GET http://localhost:18001/openapi.json` — OpenAPI 3.1 spec for every route
- `GET http://localhost:18001/docs` — interactive Swagger UI
- `GET http://localhost:18001/redoc` — prose-style API reference

Every endpoint has a docstring describing its purpose, required fields, and expected response shape. The OpenAPI spec is the authoritative contract; when in doubt, trust it over this document.

---

## One-paragraph summary (for model context-windows)

MassClaw is a decentralized AI-agent OS at `http://localhost:18001` (peers on 18002, 18003). Discover capabilities via `GET /.well-known/agent-facts.json`. Submit plain-English tasks via `POST /api/v1/workflows/submit` with `{instruction, budget, domain, priority}`, poll `GET /api/v1/workflows/{id}/status` until terminal, fetch `GET /api/v1/workflows/{id}/result`. For high-risk tasks, handle `AWAITING_APPROVAL` via `/api/v1/approvals`. Query shared memory via `POST /api/v1/memory/query`; resolve conflicts via `POST /api/v1/memory/facts/resolve` with modes planning/audit/sensitive. Check wallet via `GET /api/v1/wallet/workflow/{id}/events`. On any error, read `next_steps` in the response.
