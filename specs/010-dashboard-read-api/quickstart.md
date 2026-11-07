# Quickstart: Dashboard Read API and MVP Page

**Feature**: 010-dashboard-read-api

## Run it

```bash
uv sync --all-groups
uv run invoke demo.up             # stack + fabric + seeded SoT + report-mode schedule
uv run invoke orchestrator.start  # worker, in its own terminal
uv run snapl dashboard            # http://127.0.0.1:18030
```

The page refreshes every 5 seconds. `curl -s localhost:18030/api/v1/overview | jq` shows the same data. The OpenAPI document is at `/api/v1/openapi.json`.

With nothing running (no stack, no worker), `snapl dashboard` still starts. Every view says what is unavailable and what to run.

## Optional

```bash
export GITHUB_TOKEN=…            # CI refreshes every 2 min instead of 10
export DASHBOARD_PORT=18030      # the registry default; change only if you must
```

## Record a live lab run

```bash
uv run invoke test-e2e            # or: uv run invoke test-integration
ls .snapl/test-runs/              # one folder per run; survives demo.reset
```

## Live evidence for the implementation PR (Principle III)

FR-012a and FR-016a change what the scan and deploy workflows record in the audit trail. FR-012a sits next to the drift diff boundary. The implementation PR states in its body what was run and observed for each item:

1. **Direction is recorded**:
   - On a fabric deployed from intent, hand-add an interface address on `leaf-01` (undesired).
   - Hand-change a BGP neighbour's peer AS on `spine-01` (missing/changed intent).
   - Run `snapl scan`.
   - **Observe**: `snapl audit --device leaf-01` shows `undesired_paths` equal to `paths`. `spine-01`'s `undesired_paths` is empty. The dashboard shows `leaf-01` as `undesired` and `spine-01` as `missing`.
2. **The demo arc, end to end**:
   - With the dashboard open, run deploy, induce drift, scan, reconcile, scan.
   - **Observe**: each step appears within 10 s (SC-001). The 24 h history shows the drifted count go 0 → 1 → 0 with a reconcile marker.
   - **Observe**: the reconcile's deploys show trigger `operator`. Their audit records carry the reconcile's id as `parent_workflow_id`. A plain `snapl deploy` carries `null` (FR-016a).
   - With a remediate-mode schedule, induce drift and wait for the next scheduled run. **Observe**: its deploys show trigger `schedule`.
   - Paste the overview JSON from after the final scan.
3. **Sources failing independently (SC-003)**:
   - Stop the worker, then Temporal (`docker compose -f development/docker-compose.yml stop temporal`), then Infrahub.
   - **Observe**: the page keeps loading, and each failure shows in its own views only, with the remedy.
4. **Old history survives**:
   - Run `invoke demo.reset`, then `invoke demo.up`.
   - **Observe**: the history view still shows the scans from item 2, and the lab evidence view still lists the run recorded before the reset.
5. **Lab evidence**:
   - Run `invoke test-e2e` on `main`, then pull three commits that land on `main` afterwards, and refresh.
   - **Observe**: "3 commits on main since the last live run", matched by `ancestor`.
   - Don't make throwaway commits on your own `main` to test this. They would be counted, and then they diverge from `origin/main`.
6. **Squash-merged evidence**:
   - Run `invoke test-integration` at the head of the implementation PR's branch, clean tree, after it is up to date with `main`. Squash-merge the PR and pull `main`.
   - **Observe**: the run is counted from the squash commit and shown as matched by `tree` (FR-028).
