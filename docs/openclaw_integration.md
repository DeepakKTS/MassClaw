# OpenClaw Integration Guide

How a stock external agent can use MassClaw with plain-English instructions and no hand-holding.

## Overview

MassClaw exposes a simple REST API that any external agent can use to:

1. **Discover** what capabilities are available
2. **Submit** a plain-English task
3. **Monitor** execution progress
4. **Retrieve** the result
5. **Read/write** shared memory
6. **Inspect** audit trails

No custom glue code, no operator intervention, no special setup.

## Base URL

```
http://localhost:8000  (local)
https://your-deployment.example.com  (production)
```

## Authentication

MassClaw supports optional authentication via JWT tokens or API keys.

**For hackathon/demo mode**, all endpoints are accessible without authentication.

**For production**, set `AUTH_REQUIRED=true` in environment and use:
```
Authorization: Bearer <jwt_token>
# or
X-API-Key: <api_key>
```

## Step 1: Discover Capabilities

```bash
GET /api/v1/capabilities
```

Response:
```json
{
  "platform": "MassClaw",
  "version": "0.1.0",
  "total_agents": 8,
  "capabilities": [
    "intake", "research", "process-analysis", "risk-assessment",
    "optimization", "cost-analysis", "report-generation", "verification"
  ],
  "domains": ["healthcare", "operations", "general"],
  "endpoints": {
    "submit_task": "POST /api/v1/tasks/submit",
    "list_agents": "GET /api/v1/agents",
    "search_agents": "GET /api/v1/agents/search?capability=research",
    "query_memory": "POST /api/v1/memory/query",
    "write_memory": "POST /api/v1/memory/write",
    "workflow_status": "GET /api/v1/workflows/{id}/status",
    "audit_trail": "GET /api/v1/audit/search"
  },
  "features": [
    "multi-agent orchestration",
    "semantic shared memory",
    "trust-aware agent selection",
    "budget-controlled execution",
    "policy-based safety",
    "real-time progress streaming"
  ]
}
```

## Step 2: Submit a Task

```bash
POST /api/v1/tasks/submit
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

## Step 3: Monitor Progress

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

## Step 4: Get Result

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

## Step 5: Read/Write Shared Memory

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

## Step 6: Inspect Audit Trail

```bash
GET /api/v1/audit/workflow/{workflow_id}
```

Returns every decision, agent assignment, trust score update, and policy evaluation for full transparency.

## Complete Flow Example

```python
import requests
import time

BASE = "http://localhost:8000/api/v1"

# 1. Discover
caps = requests.get(f"{BASE}/capabilities").json()
print(f"Available: {caps['capabilities']}")

# 2. Submit
resp = requests.post(f"{BASE}/tasks/submit", json={
    "instruction": "Research the impact of AI on radiology workflows",
    "budget": 400
})
wf_id = resp.json()["workflow_id"]

# 3. Poll until done
while True:
    status = requests.get(f"{BASE}/workflows/{wf_id}/status").json()
    print(f"Progress: {status['progress_percent']}%")
    if status["status"] in ("completed", "failed", "cancelled"):
        break
    time.sleep(5)

# 4. Get result
result = requests.get(f"{BASE}/workflows/{wf_id}/result").json()
print(result["result"]["content"])
```

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
