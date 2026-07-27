# snapl Demo Scenarios

Repeatable, demo-ready walkthroughs of the closed NAF loop: **Intent → Deploy → Collect → Verify → Observe → Reconcile**, with a durable audit trail under everything.

Each scenario states its preconditions, the exact steps, what you should see, and what to put on screen in the Temporal Web UI. They are ordered as a narrative — run top to bottom for a full demo, or cherry-pick.

> **Only scenarios 5 and 8 work without the lab** — cancellation resolves before any device call has to succeed, and the audit log is read from SQLite. Every other scenario asserts a successful deploy or a real drift reading, including scenario 4, whose "1/2 succeeded" needs the one real device to actually reconcile. Bring the fabric up.

---

## One-time setup

```bash
# 1. Dependencies
uv sync --all-groups

# 2. Environment — the committed defaults work as-is
cp development/.env.example development/.env

# 3. Everything else: compose stack + SR Linux fabric + seeded SoT + preflight
uv run invoke demo.up
```

`demo.up` is `dev.deps` → `dev.lab-deploy` → `demo.seed` → `demo.check`. Each is
runnable on its own; all are idempotent, so re-run any of them freely. It ends
by printing a preflight report — do not start demoing until every line is `ok`:

```
Demo preflight:
  [ok] temporal reachable — localhost:7233
  [ok] source of truth reachable — http://localhost:8000
  [ok] 'dcfabric' seeded — 6 devices
  [ok] gnmi spine-01 — 172.20.21.11:57400
  ...
All 9 checks passed — ready to demo.
```

Start the worker in its own terminal (leave it visible — its log narrates every demo):

```bash
uv run invoke orchestrator.start
```

### Ports and credentials

Everything below assumes the **committed defaults**: Infrahub on `8000`, Temporal
on `7233` (Web UI `8233`), SR Linux gNMI on `57400` with `admin` / `NokiaSrl1!`.
Those are what a clean checkout gets, and `development/.env.example` sets them.

If you offset the host ports to run alongside another project's stack, set
`INFRAHUB_ADDRESS` and `TEMPORAL_HOST` in `development/.env` to match — the
worker and the demo tasks both read them, and `demo.check` will tell you if
they disagree with what is actually listening.

Device dial targets come from the SoT (`lab_node_name`, seeded as each node's
static `172.20.21.x` address) — nothing to configure, and nothing to patch by
hand. See `containerlab/README.md` for the addressing rationale.

Shared snippet used by every scenario — a client plus the device inventory:

```python
import asyncio, os
from snapl_intent.infrahub.client import build_client
from snapl_intent.infrahub.store import InfrahubIntentStore
from snapl_orchestrator.worker.client import build_client as build_temporal_client

TASK_QUEUE = "snapl-orchestrator"

async def connect():
    # build_client, not Client.connect — it registers the pydantic data
    # converter the workflows' payloads are encoded with.
    return await build_temporal_client(target=os.environ.get("TEMPORAL_HOST", "localhost:7233"))

async def device_ids():
    client = build_client()   # reads INFRAHUB_ADDRESS / INFRAHUB_API_TOKEN
    store = InfrahubIntentStore(client=client)
    states = await store.get_desired_state(use_case="dcfabric")
    return {s.device.name: s.device.id for s in states}
```

---

## Scenario 1 — Closed-loop deploy (happy path)

**Story:** "We declare intent in the Source of Truth; snapl makes it real and *proves* it took effect — deploy isn't done until the device's running config verifies clean."

```python
from snapl_orchestrator.workflows.deploy_intent import DeployIntentWorkflow
from temporalio.client import WorkflowIDConflictPolicy

async def main():
    client, ids = await connect(), await device_ids()
    result = await client.execute_workflow(
        DeployIntentWorkflow.run,
        ids["spine-01"],
        id=f"deploy-intent-{ids['spine-01']}",
        task_queue=TASK_QUEUE,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )
    print(result.success, result.reason, result.ended_at - result.started_at)

asyncio.run(main())
```

**Expect:** `True succeeded 0:00:0X`. The worker log shows the four activities in order: `fetch_desired_state → apply_config → collect_running_state → detect_drift`.

**Temporal UI:** open the `deploy-intent-…` workflow → Event History. Every activity, retry, input and output payload is durably recorded — this history *is* the deploy receipt.

---

## Scenario 2 — Fabric-wide drift detection

**Story:** "Someone changes a device by hand. snapl doesn't just notice *that* something changed — it names the exact config paths."

1. Make an out-of-band change on one device:

```bash
docker exec -it clab-dcfabric-spine-01 sr_cli \
  "enter candidate; interface ethernet-1/1 admin-state disable; commit now"
```

2. Scan the whole use case:

```python
from uuid import uuid4
from snapl_orchestrator.workflows.scan_drift import ScanDriftWorkflow

async def main():
    client = await connect()
    scan = await client.execute_workflow(
        ScanDriftWorkflow.run, "dcfabric",
        id=f"scan-drift-dcfabric-{uuid4()}", task_queue=TASK_QUEUE,
    )
    print(f"{scan.total} devices: {scan.clean} clean, {scan.drifted} drifted, {scan.errored} errored")
    for report in scan.reports.values():
        for item in report.items:
            print(f"  {report.device_name}: {item.path} desired={item.desired} actual={item.actual}")

asyncio.run(main())
```

**Expect:** `6 devices: 5 clean, 1 drifted, 0 errored` and the precise line:
`spine-01: /interface[name=ethernet-1/1]/admin-state desired=enable actual=disable`.

**Temporal UI:** one ScanDrift workflow fanned out activities for all six devices in parallel.

---

## Scenario 3 — Self-healing (reconcile back to clean)

**Story:** "Detection without remediation is a dashboard. snapl closes the loop — and each repair is itself a full verified deploy."

```python
from uuid import uuid4
from snapl_orchestrator.workflows.reconcile_devices import ReconcileDevicesWorkflow

async def main():
    client, ids = await connect(), await device_ids()
    result = await client.execute_workflow(
        ReconcileDevicesWorkflow.run,
        [ids["spine-01"]],                      # the drifted device from Scenario 2
        id=f"reconcile-{uuid4()}", task_queue=TASK_QUEUE,
    )
    print(f"{result.succeeded}/{result.total} succeeded, {result.failed} failed, {result.skipped} skipped")

asyncio.run(main())
```

**Expect:** `1/1 succeeded, 0 failed, 0 skipped`. Re-run the Scenario 2 scan: `6 clean, 0 drifted` — the loop is closed.

**Temporal UI:** the Reconcile workflow spawned a **child** DeployIntent workflow per device — point out the parent/child link.

---

## Scenario 4 — A device vanishes from the SoT (skip, not fail)

**Story:** "Operational reality: the reconcile list can be stale. A device that no longer exists in the SoT is *skipped* with an audit note — not falsely reported as a failure." (Behaviour fixed in [#15](https://github.com/anton-tvrz/snapl/issues/15).)

Run Scenario 3's snippet with one real id and one fabricated one:

```python
from uuid import uuid4
ids_list = [ids["leaf-01"], uuid4()]   # second id exists nowhere in the SoT
```

**Expect:** `1/2 succeeded... 0 failed, 1 skipped` — and the missing id is absent from `result.device_results`. The real device still reconciled normally.

---

## Scenario 5 — Operator cancels an in-flight workflow

**Story:** "An operator can pull the cord at any time, and the audit log shows the cancellation as a first-class event — not a mystery gap." (Behaviour fixed in [#16](https://github.com/anton-tvrz/snapl/issues/16).)

```python
async def main():
    client, ids = await connect(), await device_ids()
    handle = await client.start_workflow(          # start_, not execute_ — don't await completion
        DeployIntentWorkflow.run, ids["leaf-02"],
        id=f"deploy-intent-{ids['leaf-02']}", task_queue=TASK_QUEUE,
    )
    await handle.cancel()
    result = await handle.result()
    print(result.success, result.reason)           # False cancelled

asyncio.run(main())
```

**Expect:** `False cancelled`, and Scenario 8's audit query for this workflow shows a `workflow_cancelled` event. (Cancelling a *Reconcile* propagates to its child deploys and re-raises — the workflow shows as Cancelled in the UI, with the audit event still recorded first.)

---

## Scenario 6 — Failure isolation in a scan

**Story:** "One broken device must not blind you to the other five."

1. Stop one node: `docker stop clab-dcfabric-leaf-04`
2. Run the Scenario 2 scan.

**Expect:** `6 devices: 5 clean, 0 drifted, 1 errored` — the unreachable device lands in `errored` with the collect failure message in its report; every other device was still evaluated.

3. Restore it: `docker start clab-dcfabric-leaf-04`

---

## Scenario 7 — Durability: kill the worker mid-flight

**Story:** "This is why the orchestrator is Temporal and not a script: the workflow survives the death of the thing executing it."

1. Start a deploy (Scenario 1) and immediately `Ctrl-C` the worker terminal.
2. The workflow in the Temporal UI stays **Running** — nothing is lost, it's waiting for a worker.
3. Restart: `uv run invoke orchestrator.start`.

**Expect:** the workflow resumes exactly where it stopped (already-completed activities are *not* re-executed — show the Event History) and runs to `succeeded`.

---

## Scenario 8 — The audit trail

**Story:** "Every demo above left a durable, queryable record — per workflow and per device, across workflow types."

```python
from snapl_orchestrator.audit.sqlite import SqliteAuditLog

async def main():
    # Same DB the worker writes to — SNAPL_AUDIT_DB, default ./snapl-audit.sqlite
    log = SqliteAuditLog(database_url=os.environ.get("SNAPL_AUDIT_DB", "./snapl-audit.sqlite"))
    await log.initialize()
    ids = await device_ids()

    for e in await log.query_by_workflow(f"deploy-intent-{ids['spine-01']}"):
        print(f"[{e.timestamp}] {e.event_type} {e.activity_name or ''} {e.outcome or ''}")

    print(len(await log.query_by_device(ids["spine-01"])), "events for spine-01 across all workflows")

asyncio.run(main())
```

**Expect:** the deploy reads as a story — `workflow_started`, four `activity_completed`, `workflow_terminated outcome=success`; the cancelled workflow from Scenario 5 ends in `workflow_cancelled` instead. The per-device query aggregates deploys, scans and reconciles that touched the device.

---

## Suggested 10-minute demo arc

1. **Deploy** (Scenario 1) — establish the loop. *2 min*
2. **Break it by hand, detect** (Scenario 2) — the "aha": exact paths named. *2 min*
3. **Reconcile, rescan clean** (Scenario 3) — closed loop. *2 min*
4. **Kill the worker mid-deploy** (Scenario 7) — durability is the differentiator. *2 min*
5. **Audit log** (Scenario 8) — everything you just did, replayed from SQLite. *2 min*

Scenarios 4–6 are the Q&A reserve: "what if the SoT is stale / someone cancels / a device is down?"

---

## Between rehearsals

```bash
uv run invoke demo.check     # is it still ready? (safe anytime, changes nothing)
uv run invoke demo.reset     # destroy lab + stack + volumes
uv run invoke demo.up        # rebuild from scratch
```

Two things reset on their own and will surprise you otherwise:

- **Temporal is an in-memory dev server** — restarting the container empties the
  Web UI of all history (#81). Re-run a scan and a reconcile to repopulate it
  before demoing the UI.
- **SR Linux nodes keep their pushed config** across a container restart, but
  not across `lab-destroy`. After a redeploy the fabric is unconfigured, so
  Scenario 1's deploy is doing real work again — which is what you want.

If a scenario misbehaves, run `demo.check` first: an unseeded SoT, a stopped
node, or a port mismatch all show up there as a named failing line.

## Known limitations to name before you are asked

- **Config removed from intent is not removed from the device** — apply is a
  merge-only gNMI update at `/` (#65).
- **Config that exists only on the device is never flagged** — the drift diff
  compares intent against live for paths intent knows about, so an
  operator-added interface or BGP neighbor goes unreported (#54).

Together these mean "we converge to intent" is currently true for additions and
changes, not deletions. Say it before the audience finds it.
