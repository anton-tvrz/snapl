"""The committed Prometheus scrape config (spec 012 T023, research R6).

Both targets run on the host, not in Compose, so they are reached through
host.docker.internal on the ports the host resource registry gives them. A
target that isn't running shows as `up == 0`, which is what the dashboard's
worker-up and source-health views read.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

CONFIG_PATH = Path(__file__).resolve().parents[3] / "development" / "prometheus" / "prometheus.yml"


@pytest.fixture(scope="module")
def config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text())


def _jobs(config: dict) -> dict[str, dict]:
    return {job["job_name"]: job for job in config["scrape_configs"]}


def test_exactly_the_worker_and_the_dashboard_are_scraped(config: dict) -> None:
    targets = {name: job["static_configs"][0]["targets"] for name, job in _jobs(config).items()}

    assert targets == {
        "snapl-worker": ["host.docker.internal:18091"],
        "snapl-dashboard": ["host.docker.internal:18030"],
    }


def test_the_scrape_interval_is_15_s(config: dict) -> None:
    """The dashboard's cache and rate windows assume 15 s (SCRAPE_INTERVAL_S)."""
    from snapl_presentation.dashboard.prometheus import SCRAPE_INTERVAL_S

    assert config["global"]["scrape_interval"] == f"{SCRAPE_INTERVAL_S}s" == "15s"
    assert all("scrape_interval" not in job for job in _jobs(config).values())


def test_both_targets_use_the_default_metrics_path(config: dict) -> None:
    assert all(job.get("metrics_path", "/metrics") == "/metrics" for job in _jobs(config).values())
