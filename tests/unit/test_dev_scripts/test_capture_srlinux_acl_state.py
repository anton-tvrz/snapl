"""The SR Linux ACL capture script (spec 008 T020, research R16).

``capture(...)`` runs with a fake collector and a recorded ``run``, so no lab is
needed. The script lives in dev/scripts/, which is not a package, so it is
loaded from its path.
"""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[3] / "dev" / "scripts" / "capture_srlinux_acl_state.py"
FIXTURES = Path(__file__).resolve().parents[2] / "unit" / "test_orchestrator" / "fixtures"

CASES = (
    "baseline",
    "applied",
    "deny-deleted",
    "permit-inserted",
    "action-flipped",
    "action-flipped-applied",
    "binding-removed",
    "binding-moved",
)


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("capture_srlinux_acl_state", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeCollector:
    def __init__(self, data: dict, calls: list) -> None:
        self._data = data
        self._calls = calls

    async def collect(self, device, paths):
        from types import SimpleNamespace

        self._calls.append(("collect", device.lab_node_name, list(paths)))
        return SimpleNamespace(data=self._data)


_ACL = {
    "/acl": {
        "acl-filter": [
            {
                "name": "ot-ingress",
                "type": "ipv4",
                "statistics-per-entry": True,
                "entry": [
                    {"sequence-id": 10, "action": {"accept": {}}, "statistics": {"matched-packets": "4"}},
                    {"sequence-id": 20, "action": {"drop": {}}, "statistics": {"matched-packets": "9"}},
                ],
            }
        ],
        "interfaces": {"interface": [{"interface-id": "ethernet-1/3.0", "statistics": {"x": 1}}]},
    }
}


def test_the_cases_are_the_capture_list(script) -> None:
    assert tuple(script.CASES) == CASES


@pytest.mark.parametrize("case", CASES)
def test_each_case_writes_its_own_fixture(script, case: str) -> None:
    assert script.fixture_path(case) == FIXTURES / f"srlinux_v26_3_1_acl_{case}.json"


def test_prune_drops_every_statistics_subtree_and_keeps_the_rest(script) -> None:
    pruned = script.prune(_ACL)
    entries = pruned["/acl"]["acl-filter"][0]["entry"]
    assert entries == [
        {"sequence-id": 10, "action": {"accept": {}}},
        {"sequence-id": 20, "action": {"drop": {}}},
    ]
    assert pruned["/acl"]["acl-filter"][0]["statistics-per-entry"] is True
    assert pruned["/acl"]["interfaces"]["interface"] == [{"interface-id": "ethernet-1/3.0"}]


async def test_capture_runs_the_change_first_then_collects_acl_and_writes_the_fixture(script, tmp_path) -> None:
    calls: list = []
    out = tmp_path / "fixture.json"

    def run(argv, **kwargs):
        calls.append(("run", argv, kwargs.get("check")))

    await script.capture(
        node="172.20.22.11",
        container="clab-site-edge-site-edge-01",
        change="delete / acl acl-filter ot-ingress type ipv4 entry 20",
        out=out,
        collector=_FakeCollector(_ACL, calls),
        run=run,
    )

    assert calls[0] == (
        "run",
        [
            "docker",
            "exec",
            "clab-site-edge-site-edge-01",
            "sr_cli",
            "-ec",
            "delete / acl acl-filter ot-ingress type ipv4 entry 20",
        ],
        True,
    )
    assert calls[1] == ("collect", "172.20.22.11", ["/acl"])
    assert json.loads(out.read_text()) == script.prune(_ACL)


async def test_capture_without_a_change_runs_nothing_on_the_device(script, tmp_path) -> None:
    calls: list = []
    await script.capture(
        node="172.20.22.11",
        container="clab-site-edge-site-edge-01",
        change=None,
        out=tmp_path / "fixture.json",
        collector=_FakeCollector(_ACL, calls),
        run=lambda *_a, **_k: calls.append("run"),
    )
    assert [call[0] for call in calls] == ["collect"]


def test_an_unknown_case_is_refused(script) -> None:
    with pytest.raises(SystemExit):
        script.parse_args(["--case", "nope"])


def test_the_defaults_are_the_site_edge_router(script) -> None:
    args = script.parse_args(["--case", "baseline"])
    assert args.node == "172.20.22.11"
    assert args.container == "clab-site-edge-site-edge-01"
    assert args.change is None


def test_it_never_writes_intent_or_config_through_snapl() -> None:
    """T020: it writes nothing to Infrahub and never calls the executor."""
    tree = ast.parse(SCRIPT.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any(name.startswith("snapl_executor") for name in imported), imported
    assert not any(name.startswith("snapl_intent.infrahub") for name in imported), imported
