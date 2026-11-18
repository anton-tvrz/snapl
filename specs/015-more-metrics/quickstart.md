# Quickstart: Checking More Metrics

Items 1 to 4 need no lab. Items 5 to 8 are the live evidence and need the lab.

## Without a lab

```bash
uv run invoke dashboard.build
uv run python dev/scripts/serve_dashboard_fixtures.py --port 18932 --sources up &
node packages/presentation/ui/e2e/look.mjs http://127.0.0.1:18932 /tmp/look
```

1. **Every view has a panel.** In `full-light.png`, the metrics section has four groups and 16 panels, each with data.
2. **The fabric's history.** The in-sync, open-drift, runs and repair-duration charts match the captured arc: one drift on spine-01 and one on leaf-01, both repaired.
3. **With the metrics store down.** Start the fixture server without `--sources up`. The audit-trail panels still have data; the metrics panels give a reason and a remedy.
4. **Tests.** The Python gate, `uv run invoke dashboard.test` and `uv run invoke dashboard.e2e` pass.

## With the lab

5. **The counters.** With the demo up and the worker restarted, run `snapl scan`, then `curl -s localhost:18091/metrics | grep '^snapl_'`. The three new families are present with only the labels in the contract. **Needs the lab.**
6. **A drift, counted.** Induce one undesired path on leaf-01 and scan. `scan-results` shows leaf-01 as `undesired`, `drift-paths` its `undesired` paths. Reconcile with `snapl reconcile --drifted --yes`, and `deploy-outcomes` shows one success. That reconcile runs its own scan first, so leaf-01 is counted `undesired` twice, and a description set on an unconfigured port gives two paths per scan (`description` and `enabled`). Remove the path by hand afterwards: reconcile doesn't remove undesired config (#65). **Needs the lab.**
7. **Scan steps.** After a scheduled scan, the loop strip shows Intent, Collector and Observability as done. Compare the scan's duration with the one before this feature. **Needs the lab.**
8. **Fixtures and files.** Recapture the Prometheus fixtures for the new views (`dev/scripts/capture_prometheus_fixtures.py`), run `invoke history.archive`, open it with `snapl dashboard --archive`, and write a seven-day snapshot and record its size. **Needs the lab.**
