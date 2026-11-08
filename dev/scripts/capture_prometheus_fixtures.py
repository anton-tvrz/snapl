#!/usr/bin/env python
"""Capture Prometheus HTTP API responses as test fixtures (spec 012, research R11).

Regenerates `tests/unit/test_presentation/fixtures/prometheus/` from a running
Prometheus. Every file is `{"http_status": <int>, "body": <parsed JSON>}`, or
`body_text` for `/-/ready`. Error answers are captured, not raised: the 400s are
fixtures too.

What it asks:
- each of the dashboard's six views, with the dashboard's own PromQL
  (`render_query`), over the last hour at a 15 s step;
- a metric that doesn't exist (an empty result), and `up` as an instant query;
- malformed PromQL, and a range at step 1 since 1970 (both 400s);
- build info, readiness, and an admin snapshot.

The snapshot request makes Prometheus write `snapshots/<name>/` into its data
directory, and the HTTP API can't delete it. Remove it by hand afterwards, or
run `invoke history.purge` when the store is no longer needed.

Usage, against the dev stack (`uv run invoke dev.deps`, with the worker and the
dashboard running so there is something to capture):
    uv run python dev/scripts/capture_prometheus_fixtures.py [--address http://127.0.0.1:18090]
"""

from __future__ import annotations

import argparse
import json
import time

# Bandit B310: the URL is built from --address, a Prometheus the operator names (loopback by default).
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from snapl_presentation.dashboard.models import MetricsViewName
from snapl_presentation.dashboard.prometheus import SCRAPE_INTERVAL_S, render_query

DEFAULT_ADDRESS = "http://127.0.0.1:18090"
FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "unit" / "test_presentation" / "fixtures" / "prometheus"

# The file each view's answer is saved as. The names predate the view names, and the tests read them.
VIEW_FILES = {
    MetricsViewName.WORKFLOW_DURATION: "range_workflow_duration_p95.json",
    MetricsViewName.GNMI_DURATION: "range_gnmi_duration_p95.json",
    MetricsViewName.GNMI_ERRORS: "range_gnmi_errors.json",
    MetricsViewName.WORKER_LAG: "range_activity_schedule_to_start_p95.json",
    MetricsViewName.WORKER_UP: "range_worker_up.json",
    MetricsViewName.SOURCE_HEALTH: "range_source_up.json",
}


def _request(address: str, path: str, params: dict[str, str] | None = None, *, post: bool = False) -> dict[str, Any]:
    url = f"{address.rstrip('/')}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, method="POST" if post else "GET")  # noqa: S310 — see the B310 note above
    try:
        # The operator's own Prometheus, named by --address.
        with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310  # noqa: S310
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    text = raw.decode()
    try:
        return {"http_status": status, "body": json.loads(text)}
    except ValueError:
        return {"http_status": status, "body_text": text}


def capture(
    address: str,
    out_dir: Path,
    *,
    namespace: str = "default",
    use_case: str = "dcfabric",
    span_s: int = 3600,
    step: int = SCRAPE_INTERVAL_S,
    end: float | None = None,
) -> list[Path]:
    """Ask Prometheus at ``address`` everything the fixtures hold, and write one file per answer."""
    end = end if end is not None else time.time()
    window = max(4 * SCRAPE_INTERVAL_S, step)
    span = {"start": str(int(end - span_s)), "end": str(int(end)), "step": str(step)}
    answers: dict[str, dict[str, Any]] = {}
    for view, filename in VIEW_FILES.items():
        query = render_query(view, namespace=namespace, use_case=use_case, window_s=window)
        answers[filename] = _request(address, "/api/v1/query_range", {"query": query, **span})
    answers["range_absent_metric.json"] = _request(
        address, "/api/v1/query_range", {"query": "snapl_metric_that_does_not_exist", **span}
    )
    answers["instant_up.json"] = _request(address, "/api/v1/query", {"query": "up"})
    answers["error_bad_query.json"] = _request(address, "/api/v1/query_range", {"query": "sum(up", **span})
    answers["error_too_many_points.json"] = _request(
        address, "/api/v1/query_range", {"query": "up", "start": "0", "end": str(int(end)), "step": "1"}
    )
    answers["buildinfo.json"] = _request(address, "/api/v1/status/buildinfo")
    answers["ready.json"] = _request(address, "/-/ready")
    answers["admin_snapshot.json"] = _request(address, "/api/v1/admin/tsdb/snapshot", post=True)

    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for filename, captured in answers.items():
        path = out_dir / filename
        path.write_text(json.dumps(captured, indent=2) + "\n")
        written.append(path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--address", default=DEFAULT_ADDRESS, help=f"Prometheus to ask (default {DEFAULT_ADDRESS})")
    parser.add_argument("--out", type=Path, default=FIXTURES, help="Where to write the files")
    parser.add_argument("--namespace", default="default", help="Temporal namespace the views filter on")
    parser.add_argument("--use-case", default="dcfabric", help="Use case the gNMI views filter on")
    args = parser.parse_args()
    for path in capture(args.address, args.out, namespace=args.namespace, use_case=args.use_case):
        print(f"{json.loads(path.read_text())['http_status']}  {path}")


if __name__ == "__main__":
    main()
