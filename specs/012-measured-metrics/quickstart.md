# Quickstart: Measured Metrics

## Run it

```bash
uv run invoke dev.deps            # stack, now including Prometheus on 18090
uv run invoke orchestrator.start  # worker; logs "metrics: http://127.0.0.1:18091/metrics"
uv run snapl dashboard            # http://127.0.0.1:18030, Metrics section at the bottom
```

- `curl -s localhost:18091/metrics | grep snapl_gnmi` shows the worker's gNMI timing once a deploy or scan has run.
- `http://localhost:18090/graph` is Prometheus's own query page.
- `curl -s 'localhost:18030/api/v1/metrics/gnmi-duration' | jq` gives the dashboard's view.

## Keep and take the history

```bash
uv run invoke history.archive     # .snapl/archives/snapl-history-<ts>.tar.gz
uv run invoke dev.stop && uv run invoke history.purge   # asks before deleting .snapl/prometheus
```

## Live evidence for the implementation PRs (Principle III)

Each item names what to run and what to observe. Paste the output into the PR that closes the matching task.

1. **gNMI timing is recorded (SC-001)**:
   - With the worker on the PR's code, run `snapl deploy leaf-01`, then `snapl scan`.
   - **Observe**: within 60 s, `/api/v1/metrics/gnmi-duration` has `leaf-01` series for `configure` and `collect`. `curl localhost:18091/metrics | grep snapl_gnmi_call_duration_count` shows only the labels in data-model.md.
2. **A second worker survives a taken port (SC-004)**:
   - With the main worker running, start a branch worker: `TEMPORAL_TASK_QUEUE=snapl-012-live SNAPL_AUDIT_DB=<scratch> uv run invoke orchestrator.start`.
   - **Observe**: it logs `metrics off: port 18091 in use …` and completes a `snapl scan` sent with the same two variables.
3. **History survives a reset (SC-002)**:
   - Note a gNMI series from item 1. Run `invoke demo.reset`, then `invoke demo.up`, and start the worker.
   - **Observe**: the series from before the reset is still on the gNMI panel for the 24 h range.
4. **Metrics down is data (SC-003)**:
   - Run `docker compose -f development/docker-compose.yml stop prometheus`.
   - **Observe**: the page loads in under 2 s, every metric panel shows the reason and `uv run invoke dev.deps`, and the 010 views are unaffected. Start it again.
5. **Source outages are remembered**:
   - Stop Temporal for 2 minutes, then start it.
   - **Observe**: the `source-health` panel shows Temporal down over that interval.
6. **Archive (SC-005)**:
   - Run `invoke history.archive` with everything up.
   - **Observe**: exit 0 and all five parts in `tar -tzf`. Then:
     - `sqlite3` opens the audit copy;
     - `docker run --rm -v <extracted>/prometheus/<snap>:/prometheus prom/prometheus:v3.10.0 --storage.tsdb.path=/prometheus` starts and serves the old series;
     - every file under `export/` parses as JSON.
   - Stop the dashboard, archive again, and **observe** exit 1 with `dashboard_export` missing in the manifest.
7. **Re-capture fixtures**: re-run the capture against the real stack, now that the worker emits `configure` and `collect`. Replace `tests/unit/test_presentation/fixtures/prometheus/` and its README.
