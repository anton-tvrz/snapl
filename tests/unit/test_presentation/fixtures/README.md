# Dashboard test fixtures (spec 010)

## `audit_demo_arc.sqlite`

A real audit trail, captured from one live demo-arc run (spec 010 T013, quickstart live evidence item 1). The projection tests for the dashboard read it, so they run against what the workflows actually write, not hand-built events alone (Principle III).

**Captured**: 2026-11-07, 21:18:56–21:20:19 UTC. Branch `010-a-foundation` on base `f03523d`, with the FR-012a (`undesired_paths`) and FR-016a (`parent_workflow_id`) payload changes in the worker. The worker ran from that branch on its own task queue (`snapl-010a-live`) with a fresh audit DB, so the file holds this run and nothing else.

**Lab**: `containerlab/dcfabric.yml`, `ghcr.io/nokia/srlinux:26.3.1`, six devices, already converged before the run.

**Steps**, in order:

1. `snapl deploy <device>` for spine-01, spine-02 and leaf-01 to leaf-04. Six operator deploys, all succeeded.
2. Induced drift by hand:
   - leaf-01, undesired: `sr_cli -ec 'set / interface ethernet-1/7 description t013-hand-added'`
   - spine-01, missing intent: `sr_cli -ec 'set / interface ethernet-1/1 admin-state disable'`
3. `snapl scan --use-case dcfabric`: 4 clean, 2 drifted.
4. `snapl reconcile --use-case dcfabric --drifted --yes`. It runs its own scan first, then reconciles leaf-01 and spine-01: 2/2 succeeded.
5. `snapl scan --use-case dcfabric`: 5 clean. leaf-01 is still drifted, because undesired config is not removed (#65).
6. Afterwards, outside the recorded trail, leaf-01 was restored with `sr_cli -ec 'delete / interface ethernet-1/7'`.

**What it contains**: 74 events. 3 `ScanDrift` runs, 1 `ReconcileDevices` run and 8 `DeployIntent` runs. Six of the deploys were operator-started (`parent_workflow_id: null`) and two were the reconcile's children (`parent_workflow_id: reconcile-217b6000-…`).

**Observed per-device scan payloads**:

| device | scans | `paths` | `undesired_paths` |
| --- | --- | --- | --- |
| leaf-01 | all three | `/interface[name=ethernet-1/7]/description` | the same path |
| spine-01 | the first two | `/interface[name=ethernet-1/1]/enabled` | `[]` |

So after the last scan, leaf-01 projects as `undesired` and every other device as `in_sync`.

**Format**: the worker writes WAL mode. The file was switched to rollback-journal mode (`PRAGMA journal_mode=DELETE`) and vacuumed after capture, so it is one self-contained file. A read-only open of a WAL-mode file would leave `-shm` files in the repository during tests. The rows are unchanged.

Do not edit the file by hand. To recapture, repeat the steps above and update this README.
