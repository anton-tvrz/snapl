"""The Prometheus fixture capture script (spec 012 T036, research R11).

``capture(address, out_dir)`` runs against a stub HTTP server on a free port
that answers like Prometheus v3.10.0, including its 400s. The script lives in
dev/scripts/, which is not a package, so it is loaded from its path.
"""

from __future__ import annotations

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[3] / "dev" / "scripts" / "capture_prometheus_fixtures.py"

EXPECTED_FILES = {
    "range_workflow_duration_p95.json",
    "range_gnmi_duration_p95.json",
    "range_gnmi_errors.json",
    "range_activity_schedule_to_start_p95.json",
    "range_worker_up.json",
    "range_source_up.json",
    "range_absent_metric.json",
    "instant_up.json",
    "error_bad_query.json",
    "error_too_many_points.json",
    "buildinfo.json",
    "ready.json",
    "admin_snapshot.json",
}


def _load():
    spec = importlib.util.spec_from_file_location("capture_prometheus_fixtures", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakePrometheus:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict[str, list[str]]]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                pass

            def _send(self, status: int, body: object, content_type: str = "application/json") -> None:
                payload = body.encode() if isinstance(body, str) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _error(self, message: str) -> None:
                self._send(400, {"status": "error", "errorType": "bad_data", "error": message})

            def do_GET(self) -> None:
                url = urlsplit(self.path)
                query = parse_qs(url.query)
                fake.requests.append(("GET", url.path, query))
                promql = query.get("query", [""])[0]
                if url.path == "/-/ready":
                    self._send(200, "Prometheus Server is Ready.\n", "text/plain")
                elif url.path == "/api/v1/status/buildinfo":
                    self._send(200, {"status": "success", "data": {"version": "3.10.0"}})
                elif promql.count("(") != promql.count(")"):
                    self._error('invalid parameter "query": parse error: unclosed left parenthesis')
                elif url.path == "/api/v1/query_range" and query.get("step") == ["1"]:
                    self._error("exceeded maximum resolution of 11,000 points per timeseries")
                elif url.path == "/api/v1/query_range":
                    result = [] if "does_not_exist" in promql else [{"metric": {}, "values": [[1, "1"]]}]
                    self._send(200, {"status": "success", "data": {"resultType": "matrix", "result": result}})
                elif url.path == "/api/v1/query":
                    vector = [{"metric": {"job": "snapl-worker"}, "value": [1, "1"]}]
                    self._send(200, {"status": "success", "data": {"resultType": "vector", "result": vector}})
                else:
                    self._send(404, {"status": "error", "error": "not found"})

            def do_POST(self) -> None:
                url = urlsplit(self.path)
                fake.requests.append(("POST", url.path, {}))
                if url.path == "/api/v1/admin/tsdb/snapshot":
                    self._send(200, {"status": "success", "data": {"name": "20261108T203115Z-674e642f920efff4"}})
                else:
                    self._send(404, {})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.address = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def prometheus():
    fake = FakePrometheus()
    yield fake
    fake.close()


def test_capture_writes_every_fixture_file(prometheus: FakePrometheus, tmp_path: Path) -> None:
    _load().capture(prometheus.address, tmp_path)

    assert {p.name for p in tmp_path.iterdir()} == EXPECTED_FILES


def test_every_file_holds_the_status_and_the_body(prometheus: FakePrometheus, tmp_path: Path) -> None:
    _load().capture(prometheus.address, tmp_path)

    for path in tmp_path.iterdir():
        captured = json.loads(path.read_text())
        assert isinstance(captured["http_status"], int), path.name
        if path.name == "ready.json":
            assert set(captured) == {"http_status", "body_text"}
            assert captured["body_text"] == "Prometheus Server is Ready.\n"
        else:
            assert set(captured) == {"http_status", "body"}, path.name


def test_a_400_is_captured_not_raised(prometheus: FakePrometheus, tmp_path: Path) -> None:
    _load().capture(prometheus.address, tmp_path)

    for name in ("error_bad_query.json", "error_too_many_points.json"):
        captured = json.loads((tmp_path / name).read_text())
        assert captured["http_status"] == 400
        assert captured["body"]["status"] == "error"
    assert "11,000 points" in json.loads((tmp_path / "error_too_many_points.json").read_text())["body"]["error"]


def test_the_views_are_captured_with_the_dashboards_own_queries(prometheus: FakePrometheus, tmp_path: Path) -> None:
    """The fixtures are only evidence for the dashboard if they answer its queries (FR-011)."""
    from snapl_presentation.dashboard.models import MetricsViewName
    from snapl_presentation.dashboard.prometheus import render_query

    _load().capture(prometheus.address, tmp_path, namespace="default", use_case="dcfabric")

    asked = {q["query"][0] for method, path, q in prometheus.requests if path == "/api/v1/query_range" and "query" in q}
    for view in MetricsViewName:
        assert render_query(view, namespace="default", use_case="dcfabric", window_s=60) in asked, view


def test_an_empty_result_is_captured(prometheus: FakePrometheus, tmp_path: Path) -> None:
    _load().capture(prometheus.address, tmp_path)

    body = json.loads((tmp_path / "range_absent_metric.json").read_text())["body"]
    assert body["data"]["result"] == []


def test_the_script_is_runnable_from_the_command_line() -> None:
    text = SCRIPT.read_text()

    assert text.startswith("#!/usr/bin/env python")
    assert 'if __name__ == "__main__":' in text
