# snapl Demo Scenarios

Repeatable, demo-ready walkthroughs of the closed NAF loop: **Intent → Deploy → Collect → Verify → Observe → Reconcile**, with a durable audit trail under everything.

Each scenario states its preconditions, the exact steps, what you should see, and what to put on screen in the Temporal Web UI. They are ordered as a narrative — run top to bottom for a full demo, or cherry-pick.

> Scenarios 1–5 and 7–8 work without real devices if you accept `apply`/`collect` failing — but for a convincing demo, bring up the lab (`uv run invoke dev.lab-deploy`, see `containerlab/README.md`).

---

## One-time setup

```bash
# 1. Dependencies
uv sync --all-groups

# 2. Infrahub + backing stores (Neo4j, Redis, RabbitMQ, Postgres) + Temporal
#    (Temporal Web UI on http://localhost:8233)
uv run invoke dev.deps

# 3. SR Linux fabric (2 spines, 4 leaves) — no native containerlab needed
uv run invoke dev.lab-deploy
```

Seed the Source of Truth (idempotent, safe to re-run):

```python
# uv run python - <<'PY' ... PY, or paste into a REPL
import asyncio
from snapl_intent.infrahub.client import build_client
from snapl_intent.infrahub.store import InfrahubIntentStore

async def main():
    client = build_client(address="http://localhost:8001", api_token="<INFRAHUB_API_TOKEN>")
    store = InfrahubIntentStore(client=client)
    print(await store.provision_schema("dcfabric"))
    print(await store.seed("dcfabric"))

asyncio.run(main())
```

Start the worker in its own terminal (leave it visible — its log narrates every demo):

```bash
INFRAHUB_API_TOKEN=<token> SRLINUX_PASSWORD=<lab-password> uv run invoke orchestrator.start
```

Shared snippet used by every scenario — a client plus the device inventory:

```python
import asyncio
from temporalio.client import Client
from snapl_intent.infrahub.client import build_client
from snapl_intent.infrahub.store import InfrahubIntentStore

TASK_QUEUE = "snapl-orchestrator"

async def connect():
    return await Client.connect("localhost:7233", namespace="default")

async def device_ids():
    client = build_client(address="http://localhost:8001", api_token="<INFRAHUB_API_TOKEN>")
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
    log = SqliteAuditLog(database_url="./snapl-audit.sqlite")
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
