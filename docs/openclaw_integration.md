# OpenClaw Integration Guide

How a stock external agent can use MassClaw with plain-English instructions and no hand-holding.

## Overview

MassClaw exposes a simple REST API that any external agent can use to:

1. **Discover** what capabilities are available (NANDA AgentFacts v1)
2. **Verify** the discovery document with its integrity credential
3. **Resolve** a provider DID to its live AgentFacts document
4. **Submit** a plain-English task
5. **Monitor** execution progress
6. **Retrieve** the result
7. **Read/write** shared memory
8. **Inspect** audit trails

No custom glue code, no operator intervention, no special setup.

## Base URL

```
http://localhost:8000  (local)
https://your-deployment.example.com  (production)
```

## Authentication

MassClaw supports optional authentication via JWT tokens or API keys.

**For local development** (`AUTH_REQUIRED=false`), all endpoints are accessible without authentication.

**For production**, set `AUTH_REQUIRED=true` in environment and use:
```
Authorization: Bearer <jwt_token>
# or
X-API-Key: <api_key>
```

The accepted auth methods are also advertised inside the AgentFacts document under
`capabilities.authentication.methods`, so a stock agent can pick one without having
to read this doc.

## Step 1: Discover Capabilities (NANDA AgentFacts v1)

Every MassClaw node serves a **flat v1 AgentFacts JSON document** at its well-known URL.
This document is the NANDA-canonical identity surface — it is NOT a W3C Verifiable
Credential envelope. Consumers read top-level fields directly.

```bash
GET /.well-known/agent-facts.json
```

Response (abridged):
```json
{
  "id": "urn:agent:massclaw:instance",
  "agent_name": "urn:agent:massclaw:instance",
  "label": "MassClaw",
  "description": "Decentralized operating layer for AI agents.",
  "version": "1.0.0",
  "documentationUrl": "https://massclaw.example/docs",
  "jurisdiction": "US",
  "provider": {
    "name": "MassClaw",
    "url": "https://massclaw.example",
    "did": "did:key:z6MkjchhfUsD6mmvni8mCdXHw216Xrm9bQe2mBH1P5RDjVJG"
  },
  "endpoints": {
    "static": [
      "https://massclaw.example",
      "https://massclaw.example/api/v1",
      "https://massclaw.example/api/v1/mcp",
      "https://massclaw.example/.well-known/agent-facts.json"
    ]
  },
  "capabilities": {
    "modalities": ["text", "structured-output", "tool-use", "workflow"],
    "authentication": { "methods": ["bearer", "api-key", "agent-facts-signature"] }
  },
  "skills": [
    {
      "id": "workflow.submit",
      "description": "Submit a multi-agent workflow with optional budget and constraints.",
      "inputModes": ["text", "structured-output"],
      "outputModes": ["structured-output"],
      "latencyBudgetMs": 30000,
      "maxTokens": 16000
    },
    { "id": "workflow.status",  "description": "Read current status and progress.",
      "inputModes": ["text"], "outputModes": ["structured-output"] },
    { "id": "workflow.result",  "description": "Fetch the final result of a completed workflow.",
      "inputModes": ["text"], "outputModes": ["structured-output", "text"] },
    { "id": "memory.query",     "description": "Semantic search over shared memory.",
      "inputModes": ["text"], "outputModes": ["structured-output"] },
    { "id": "identity.resolve", "description": "Resolve a DID to a verified AgentFacts document.",
      "inputModes": ["text"], "outputModes": ["structured-output"] }
  ],
  "certification": { "level": "verified-enterprise" },
  "telemetry": { "metrics": { "availability": 0.999, "latency_p95_ms": 820.0 } },
  "verifiable_credentials": [
    {
      "type": ["AgentFactsIntegrityCredential"],
      "issuer": "did:key:z6MkjchhfUsD6mmvni8mCdXHw216Xrm9bQe2mBH1P5RDjVJG",
      "issued": "2026-04-16T09:00:00Z",
      "proof": {
        "type": "DataIntegrityProof",
        "cryptosuite": "eddsa-rdfc-2022",
        "created": "2026-04-16T09:00:00Z",
        "verificationMethod": "did:key:z6MkjchhfUsD6mmvni8mCdXHw216Xrm9bQe2mBH1P5RDjVJG#ed25519",
        "proofPurpose": "assertionMethod",
        "proofValue": "z3M...base58btc-multibase-signature..."
      }
    }
  ],
  "x-massclaw": {
    "example_requests": [
      { "name": "submit_workflow",
        "method": "POST",
        "path": "/api/v1/workflows/submit",
        "body": { "task": "Plan a dinner for 6 with vegetarian options.", "domain": "general", "budget_usd": 1.0 } },
      { "name": "poll_status",  "method": "GET",  "path": "/api/v1/workflows/{workflow_id}/status" },
      { "name": "read_result",  "method": "GET",  "path": "/api/v1/workflows/{workflow_id}/result" },
      { "name": "query_memory", "method": "POST", "path": "/api/v1/memory/query",
        "body": { "query": "what is the deadline", "k": 10 } },
      { "name": "resolve_did",  "method": "GET",
        "path": "/api/v1/identity/resolve?did=did:key:z..." }
    ],
    "error_schema": {
      "type": "object",
      "required": ["error", "message", "next_steps"],
      "properties": {
        "error": { "type": "string" },
        "message": { "type": "string" },
        "next_steps": { "type": "array", "items": { "type": "string" } },
        "correlation_id": { "type": "string" }
      }
    },
    "nanda_index_handle": "massclaw"
  }
}
```

Key properties a stock agent cares about:

- `provider.did` — the anchor the integrity credential is bound to (a `did:key` by default).
- `endpoints.static[]` — the list of callable base URLs. Pick any; `/api/v1` is the REST surface.
- `capabilities.authentication.methods` — what auth headers to send.
- `skills[]` — stable `id` strings you can filter on (e.g. `workflow.submit`).
- `x-massclaw.example_requests[]` — copy-paste-shaped request templates a stock agent can execute directly.
- `x-massclaw.error_schema` — every error response from MassClaw conforms to this shape (see below).

For a specific registered agent, the same v1 shape is served at:

```bash
GET /api/v1/agents/{agent_id}/agent-facts.json
```

## Step 2: Verify the AgentFacts Signature

Each AgentFacts document MAY carry one or more `verifiable_credentials[]` entries of
type `AgentFactsIntegrityCredential`. These use a W3C **DataIntegrityProof** block
with cryptosuite `eddsa-rdfc-2022`, signing a canonicalised hash of the document
with the `verifiable_credentials` array cleared.

You can verify locally (`did:key` anchors the Ed25519 public key right inside the DID
identifier — no external fetch required) or delegate verification to MassClaw:

```bash
POST /api/v1/agents/verify-facts
Content-Type: application/json

<full AgentFacts JSON document body>
```

Response:
```json
{
  "valid": true,
  "errors": [],
  "provider_did": "did:key:z6Mkjchhf...",
  "agent_id": "urn:agent:massclaw:instance",
  "label": "MassClaw"
}
```

On failure, `valid` is `false` and `errors[]` lists every structural or cryptographic
issue (missing credential, wrong cryptosuite, signature mismatch, schema violation).
No database state is read or written — this endpoint is a pure cryptographic check,
safe to call repeatedly.

## Step 3: Resolve a Provider DID

Once you have a provider DID (e.g. found via a NANDA Index lookup, passed by another
agent, or pulled from an AgentFacts document you already have), you can resolve it
to a fresh AgentFacts document through any MassClaw node:

```bash
GET /api/v1/identity/resolve?did=did:key:z6Mkj...
# optional: pass a well-known URL hint if the Index doesn't know the DID yet
GET /api/v1/identity/resolve?did=did:key:z6Mkj...&well_known_url_hint=https://peer.example/.well-known/agent-facts.json
```

Response:
```json
{
  "did": "did:key:z6Mkjchhf...",
  "source": "nanda_index",
  "agent_facts": { "...": "full AgentFacts v1 document" }
}
```

`source` is one of:

- `local` — the DID matches an agent registered on this MassClaw node.
- `nanda_index` — resolved via the NANDA Index (`agent_facts_link` pointer).
- `well_known` — fetched from the supplied `well_known_url_hint` URL.
- `cache` — Redis-cached result from a prior resolution.

Errors follow the standard shape. Malformed DIDs return `400 malformed_did`;
unresolvable DIDs return `404 did_not_resolvable`.

## Step 4: Submit a Task

```bash
POST /api/v1/workflows/submit
Content-Type: application/json

{
  "instruction": "Analyze outpatient scheduling bottlenecks and propose improvements",
  "budget": 500
}
```

Optional fields:
```json
{
  "instruction": "...",
  "budget": 500,
  "domain": "healthcare",
  "priority": 5,
  "user_id": "external-agent-123"
}
```

Response:
```json
{
  "workflow_id": "abc123-...",
  "status": "pending",
  "message": "Task accepted. Use workflow_id to track progress.",
  "track_url": "/api/v1/workflows/abc123-.../status"
}
```

## Step 5: Monitor Progress

### Poll Status
```bash
GET /api/v1/workflows/{workflow_id}/status
```

Response:
```json
{
  "workflow_id": "abc123-...",
  "status": "running",
  "progress_percent": 45,
  "total_tasks": 7,
  "completed_tasks": 3,
  "running_tasks": 2,
  "failed_tasks": 0,
  "budget_used": 124.5,
  "budget_limit": 500,
  "elapsed_seconds": 42.3
}
```

### Real-time SSE Stream
```bash
GET /api/v1/workflows/{workflow_id}/stream
```
Returns Server-Sent Events for live progress updates.

## Step 6: Get Result

```bash
GET /api/v1/workflows/{workflow_id}/result
```

Response:
```json
{
  "workflow_id": "abc123-...",
  "status": "completed",
  "result": {
    "content": "## Analysis of Outpatient Scheduling...",
    "confidence": 0.87,
    "summary": "7 agents collaborated across research, analysis, and synthesis"
  }
}
```

## Step 7: Read/Write Shared Memory

### Write
```bash
POST /api/v1/memory/write
Content-Type: application/json

{
  "workflow_id": "abc123-...",
  "source_agent_id": "your-agent-id",
  "memory_type": "fact",
  "content": "Hospital X has 300 outpatient visits per day",
  "confidence": 0.95
}
```

### Semantic Search
```bash
POST /api/v1/memory/query
Content-Type: application/json

{
  "query": "outpatient scheduling capacity",
  "min_similarity": 0.5,
  "top_k": 5
}
```

## Step 8: Inspect Audit Trail

```bash
GET /api/v1/audit/workflow/{workflow_id}
```

Returns every decision, agent assignment, trust score update, and policy evaluation for full transparency.

## Error Responses

Every MassClaw error — including those from the identity surface — conforms to the
schema advertised in `x-massclaw.error_schema`:

```json
{
  "error": "did_not_resolvable",
  "message": "DID did:key:z6Mk... is not registered locally, in the NANDA Index, or at any supplied well-known URL.",
  "next_steps": [
    "Confirm the DID is registered on this MassClaw node, the NANDA Index, or accessible at a well-known URL.",
    "If you know the facts URL, pass it via the 'hint' query parameter."
  ],
  "correlation_id": "f3b1a7c9-..."
}
```

Judges and stock agents should treat any non-2xx response as having this shape and
use `error` for machine logic, `message` for human output, and `next_steps[]` as a
hint for automated recovery. `correlation_id` echoes back in logs for support.

## Complete Flow Example

A fully stock OpenClaw agent can execute an end-to-end workflow with nothing but
the well-known URL:

```python
import requests
import time

WELL_KNOWN = "http://localhost:8000/.well-known/agent-facts.json"

# 1. Discover — fetch the v1 AgentFacts document
facts = requests.get(WELL_KNOWN).json()

# 2. (Optional) verify the integrity credential server-side
verify_base = facts["endpoints"]["static"][1]  # .../api/v1
verification = requests.post(f"{verify_base}/agents/verify-facts", json=facts).json()
assert verification["valid"], verification["errors"]
provider_did = verification["provider_did"]

# 3. Read the REST base URL from endpoints.static, and the example requests
rest_base = next(u for u in facts["endpoints"]["static"] if u.endswith("/api/v1"))
examples = {e["name"]: e for e in facts["x-massclaw"]["example_requests"]}

# 4. Submit a workflow using the template the AgentFacts gave us
submit = examples["submit_workflow"]
body = dict(submit["body"])
body["task"] = "Research the impact of AI on radiology workflows"
body["budget_usd"] = 400
resp = requests.request(
    submit["method"],
    f"{rest_base}{submit['path']}",
    json=body,
)
resp.raise_for_status()
wf_id = resp.json()["workflow_id"]

# 5. Poll until done, using the template path
poll = examples["poll_status"]
while True:
    r = requests.request(
        poll["method"],
        f"{rest_base}{poll['path'].format(workflow_id=wf_id)}",
    )
    if r.status_code >= 400:
        err = r.json()           # matches x-massclaw.error_schema
        raise RuntimeError(f"{err['error']}: {err['message']}")
    status = r.json()
    print(f"Progress: {status['progress_percent']}%")
    if status["status"] in ("completed", "failed", "cancelled"):
        break
    time.sleep(5)

# 6. Retrieve the result
read = examples["read_result"]
result = requests.request(
    read["method"],
    f"{rest_base}{read['path'].format(workflow_id=wf_id)}",
).json()
print(result["result"]["content"])

# 7. (Optional) resolve the provider DID later, from any MassClaw node
resolved = requests.get(
    f"{rest_base}/identity/resolve",
    params={"did": provider_did},
).json()
assert resolved["source"] in ("local", "nanda_index", "well_known", "cache")
```

Notice the agent never hard-codes a single MassClaw URL or request body: every
path and template comes from the AgentFacts document itself.

## What MassClaw Does Internally

When you submit a task, MassClaw automatically:

1. **Interprets** the goal (intent, constraints, risk level, complexity)
2. **Plans** execution strategy (direct response, DAG pipeline, iterative, verify-and-refine)
3. **Decomposes** into subtasks with dependencies
4. **Selects** best agents by trust score, capability match, cost, and latency
5. **Reserves** budget before executing
6. **Executes** tasks in parallel where possible
7. **Writes** results to shared memory
8. **Updates** trust scores based on performance
9. **Synthesizes** a final combined result
10. **Logs** every decision to the audit trail

No manual orchestration needed. The external agent just submits an instruction and gets back a result.
