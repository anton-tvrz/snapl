# Contract: Worker Metrics Settings and History Tasks

**Feature**: 012-measured-metrics

## Worker settings (environment)

| variable | default | meaning |
| --- | --- | --- |
| `WORKER_METRICS_PORT` | `18091` | port for the SDK runtime's Prometheus endpoint; `off` disables metrics |
| `WORKER_METRICS_BIND` | `127.0.0.1` | bind address; Linux Docker hosts may need `0.0.0.0` (research R2) |

At startup the worker logs one line, either:

- `metrics: http://127.0.0.1:18091/metrics`, or
- `metrics off: port 18091 in use (set WORKER_METRICS_PORT to move it, or off)`.

The worker never exits because of metrics (FR-002).

## Dashboard settings (environment)

| variable | default |
| --- | --- |
| `PROMETHEUS_ADDRESS` | `http://localhost:${PROMETHEUS_PORT:-18090}` |

## Compose

- **Service:** `prometheus`, image `prom/prometheus:v3.10.0`.
- **Port:** `127.0.0.1:${PROMETHEUS_PORT:-18090}:9090`.
- **Data:** `../.snapl/prometheus` bind-mounted to `/prometheus`.
- **Config:** `./prometheus/prometheus.yml` mounted read-only.
- **Flags:** `--storage.tsdb.retention.size=${PROMETHEUS_RETENTION_SIZE:-2GB}` and `--web.enable-admin-api`.
- **Host:** `extra_hosts: ["host.docker.internal:host-gateway"]`.

## `invoke history.archive`

```
uv run invoke history.archive [--use-case dcfabric]
```

- **Output:** `${SNAPL_ARCHIVE_DIR:-.snapl/archives}/snapl-history-<YYYYMMDDTHHMMSSZ>.tar.gz`, written via a `.partial` file and renamed when complete.
- **Contents:**
  - `manifest.json`
  - `audit/snapl-audit.sqlite`
  - `prometheus/<snapshot name>/…`
  - `test-runs/…`
  - `export/{overview,history,links,sources}.json`
  - `export/metrics/<view>.json`
- **Exit code:** 0 when every part is present, 1 when any part is missing. The archive is still written, and the missing parts are named on stderr and in the manifest.
- **Prints:** the archive's path and size, then one line per part.

## `invoke history.purge`

```
uv run invoke history.purge [--yes]
```

- **Refuses (exit 1)** while Prometheus answers on `PROMETHEUS_PORT`, with `uv run invoke dev.stop`.
- **Prompts** otherwise: it shows the directory and its size and asks for `purge` to be typed. Anything else aborts with exit 0 and deletes nothing.
- **Without a TTY:** refuses unless `--yes` is given.
- **Deletes** `.snapl/prometheus/` only.
