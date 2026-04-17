# Federation Demo Runbook

This document is the one-minute path for a judge, collaborator, or curious
operator to watch MassClaw's CRDT federation reconcile in real time on a
laptop. No cloud account, no signup, no API keys required.

## What you will see

A 3-node MassClaw federation runs in Docker. You write a handful of signed
memory records on different nodes, deliberately partition one node, write
**conflicting** records on each side of the split, reconnect the network,
and watch the three Merkle roots converge within a handful of seconds.
Every record is Ed25519-signed by the originating node's instance key, so
the records that appear on a peer **really** came from the originator —
verified on the wire.

This is the Phase 1 proof that the entire federation stack (identity,
CRDT write path, content-addressed memory, Merkle sync, peer-auth, and
gossip) works together end-to-end.

## Prerequisites

- **Docker** 20.10.13+ (Docker Desktop, Colima, or podman with a `docker`
  alias). `docker compose` must work — run `docker compose version` to
  confirm.
- **curl** (preinstalled on macOS and every Linux distro).
- **~2 GB free RAM** for the 12 containers (3 × {postgres, redis, backend,
  worker}).
- *(Optional but recommended)* `jq` and `python3` for prettier script
  output. All scripts fall back to plain curl + sed if either is missing.

No Python packages, no Node.js, no keys to generate.

## One-command walkthrough

```bash
./scripts/demo_federation_test.sh
```

That script runs every scenario below back-to-back and asserts the
outcomes. Exit code 0 = every assertion passed.

## Step-by-step (what the script is actually doing)

### 1. Bring up the federation

```bash
./scripts/demo_up.sh
```

- Builds a single Docker image for the backend + worker.
- Launches 12 containers organised as three independent stacks
  (node-a / node-b / node-c), each with its own Postgres, Redis, FastAPI
  backend, and Celery worker+beat.
- Each backend runs `alembic upgrade head` on boot so the database is
  migration-current before uvicorn starts.
- Each backend generates its own Ed25519 instance keypair (persisted in
  an in-container tmpfs so restarts don't change the identity).
- The three nodes discover each other through the
  `MASSCLAW_FEDERATION_PEERS` env var set in `docker/federation.yml`.
- The Celery beat schedule is set to 5 seconds for the demo (production
  default is 30s).
- A shared demo workflow UUID is seeded on all three nodes so the
  gossiped records have something to FK-resolve against.

Ports on your host:

| Node | Backend | Postgres | Redis |
|------|---------|----------|-------|
| node-a | http://localhost:18001 | 15441 | 16381 |
| node-b | http://localhost:18002 | 15442 | 16382 |
| node-c | http://localhost:18003 | 15443 | 16383 |

### 2. Write a record

```bash
./scripts/demo_write.sh node-a "deadline: April 30" --confidence 0.9
```

Prints the content hash and the `did:key:...` that signed it. Under the
hood this calls `POST /api/v1/demo/self-sign-write` on the specified
node; that endpoint uses the node's instance key to sign a canonical
body derived from the CanonicalBody schema and persists the record
through `CRDTStore.put()`. Within ≤5 seconds the next gossip tick picks
it up and propagates to the other two nodes.

### 3. Watch the Merkle roots

```bash
./scripts/demo_summary.sh           # one-shot
./scripts/demo_summary.sh --watch 2 # refresh every 2 seconds
```

Each node's Merkle root is a SHA-256 over its 256 bucket digests. When
all three roots match, federation is in sync. While gossip is still
catching up, the roots disagree; the divergent bucket indices tell
peers exactly which subset of records to pull.

### 4. Partition one node

```bash
./scripts/demo_partition.sh node-c
```

This is a **real** partition: `docker network disconnect` drops node-c's
backend + worker from the `massclaw-fed-network` bridge. Node-c is still
reachable from your host (port 18003), but it can no longer talk to
peers.

### 5. Write conflicting records

```bash
./scripts/demo_write.sh node-c "deadline: April 28" --confidence 0.71   # offline write
./scripts/demo_write.sh node-b "deadline: May 15"   --confidence 0.88   # reaches node-a, not node-c
```

### 6. Heal the partition

```bash
./scripts/demo_heal.sh node-c
```

Re-attaches node-c to the federation network. Within a few gossip ticks
node-c learns about the records written while it was offline, and
node-a + node-b learn about node-c's offline write.

### 7. Resolve a fact across the CRDT set

```bash
./scripts/demo_query.sh node-a "what is the deadline" --mode audit
```

The `audit` mode returns every candidate with its rank, similarity, and
effective author trust. `planning` picks the top-ranked winner.
`sensitive` refuses to pick when two candidates tie at high confidence
and sets `requires_hitl=true` so the UI can route the question to a
human.

### 8. Tear down

```bash
./scripts/demo_down.sh
```

Stops every container and removes the network. The tmpfs volumes mean
state vanishes on teardown — perfect for repeatable demos.

## Troubleshooting

- **`docker compose` reports "cannot resolve host massclaw-fed-*"**:
  the network name is fixed (`massclaw-fed-network`). If another compose
  project previously created a conflicting network, run
  `docker network prune` and rerun `demo_up.sh`.
- **Backend exits with an `alembic` error**: check
  `docker compose -f docker/federation.yml logs backend-a`. The most
  common cause is Postgres not being ready in time — the healthcheck
  should prevent this, but a cold-start on a slow machine can take
  longer. Just rerun `demo_up.sh`.
- **Gossip roots never match**: confirm the worker containers are alive
  with `docker compose -f docker/federation.yml ps`. If a worker is in a
  restart loop, its logs show why (`docker compose ... logs worker-a`).
- **`demo_summary.sh` shows "unreachable"**: the peer-auth signing step
  in `scripts/lib/federation.sh` requires `python3`. Install Python 3 on
  the host (not the container) and rerun.

## What the demo proves

- **Signed provenance**: every record carries its author's DID and a
  verifiable Ed25519 signature over the canonical body. A node refuses
  to persist a record whose signature fails.
- **Content-addressed identity**: a record's hash is the SHA-256 of its
  canonical body, so two peers computing the hash of the same semantic
  write produce the same address. No coordinator is needed.
- **Peer-authenticated gossip**: each outbound sync request carries the
  caller's DID, a timestamp, and an Ed25519 signature over
  `<timestamp>|<METHOD>|<path>`. Replay is bounded at 60 seconds;
  unknown peers are rejected when the allowlist is populated.
- **Partition tolerance**: writes made on an isolated node are preserved
  and propagated when the network heals — the CRDT merges the two sets
  without picking a winner or losing a record.
- **Explicit conflict handling**: the three-mode fact resolver
  (`planning`, `audit`, `sensitive`) is how consumers decide what "the
  truth" is at read time — the CRDT itself just stores the set.

The demo is deliberately reproducible with twelve shell commands. That
is the MassClaw bet: federation does not require a PhD in distributed
systems to demo, it requires clean contracts between five well-named
modules.
