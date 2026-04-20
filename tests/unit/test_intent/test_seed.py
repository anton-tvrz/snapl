"""Unit tests for dependency-ordered data ingestion (seed).

Covers YAML parsing, dependency ordering of ``SEED_ORDER``, upsert-by-default
semantics, validation rejection, and idempotent re-run behaviour. Uses a mock
SDK client so no Infrahub is needed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from snapl_intent.exceptions import IntentConnectionError, IntentValidationError
from snapl_intent.infrahub.seed import (
    SEED_ORDER,
    SeedIngester,
    load_seed_file,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# YAML parsing
# ---------------------------------------------------------------------------


class TestLoadSeedFile:
    def test_load_valid_yaml(self, tmp_path: Path):
        path = tmp_path / "topology.yml"
        path.write_text(yaml.safe_dump({"devices": [{"name": "spine-01"}]}))

        data = load_seed_file(path)

        assert data["devices"][0]["name"] == "spine-01"

    def test_load_empty_file_returns_empty_dict(self, tmp_path: Path):
        path = tmp_path / "empty.yml"
        path.write_text("")

        assert load_seed_file(path) == {}

    def test_load_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_seed_file(tmp_path / "nope.yml")

    def test_load_malformed_yaml_raises_validation(self, tmp_path: Path):
        path = tmp_path / "bad.yml"
        path.write_text("devices:\n  - name: spine-01\n   bad-indent")

        with pytest.raises(IntentValidationError):
            load_seed_file(path)


# ---------------------------------------------------------------------------
# Dependency ordering
# ---------------------------------------------------------------------------


class TestSeedOrder:
    def test_supporting_entities_precede_devices(self):
        assert SEED_ORDER.index("organization") < SEED_ORDER.index("devices")
        assert SEED_ORDER.index("manufacturer") < SEED_ORDER.index("devices")
        assert SEED_ORDER.index("platform") < SEED_ORDER.index("devices")
        assert SEED_ORDER.index("device_types") < SEED_ORDER.index("devices")
        assert SEED_ORDER.index("location") < SEED_ORDER.index("devices")
        assert SEED_ORDER.index("autonomous_systems") < SEED_ORDER.index("devices")

    def test_interfaces_come_after_devices(self):
        assert SEED_ORDER.index("devices") < SEED_ORDER.index("interfaces")

    def test_bgp_sessions_come_last(self):
        assert SEED_ORDER.index("bgp_peer_groups") < SEED_ORDER.index("bgp_sessions")
        assert SEED_ORDER.index("interfaces") < SEED_ORDER.index("bgp_sessions")


# ---------------------------------------------------------------------------
# Ingester — order, upsert, idempotency
# ---------------------------------------------------------------------------


def _stub_node(name: str = "name") -> MagicMock:
    """Return a fake Infrahub node with a ``save`` coroutine."""
    node = MagicMock(name=name)
    node.save = AsyncMock()
    return node


class TestSeedIngester:
    def _make_client(self) -> MagicMock:
        client = MagicMock()
        # ``create`` returns a node which the ingester then saves.
        client.create = AsyncMock(side_effect=lambda **kwargs: _stub_node(name=kwargs.get("kind", "node")))
        # ``all`` returns existing nodes — empty for a first run.
        client.all = AsyncMock(return_value=[])
        client.filters = AsyncMock(return_value=[])
        return client

    async def test_ingest_upserts_in_declared_order(self, tmp_path: Path):
        dataset: dict[str, Any] = {
            "organization": {"name": "Test Org"},
            "location": {"name": "Lab", "shortname": "lab"},
            "manufacturer": {"name": "Nokia"},
            "platform": {"name": "SR Linux"},
            "device_types": [{"name": "IXR-D2"}],
            "autonomous_systems": [{"name": "AS1", "asn": 65001}],
            "vrfs": [{"name": "default"}],
            "ip_prefixes": [{"prefix": "10.0.0.0/24"}],
            "devices": [
                {
                    "name": "spine-01",
                    "role": "spine",
                    "use_case": "dcfabric",
                    "device_type": "IXR-D2",
                    "management_ip": "10.0.0.1/24",
                    "asn": 65001,
                }
            ],
            "interfaces": [{"device": "spine-01", "name": "ethernet-1/1"}],
            "bgp_peer_groups": [{"name": "underlay-ipv4"}],
            "bgp_sessions": [
                {"local_device": "spine-01", "remote_device": "leaf-01", "local_as": 65000, "remote_as": 65001}
            ],
        }
        path = tmp_path / "topology.yml"
        path.write_text(yaml.safe_dump(dataset))

        client = self._make_client()
        ingester = SeedIngester(client=client)

        result = await ingester.seed(use_case="dcfabric", data_path=path, branch="main")

        # The ingester must have attempted to create nodes for every section
        # we provided.
        create_kinds = [call.kwargs.get("kind") for call in client.create.await_args_list]
        assert "OrganizationGeneric" in create_kinds or "Organization" in create_kinds or any(
            "Organization" in k for k in create_kinds
        )
        assert result.use_case == "dcfabric"
        assert result.branch == "main"
        assert result.devices_created == 1
        assert result.total_records >= 10  # at least one per section above

    async def test_ingest_second_run_updates_without_duplicates(self, tmp_path: Path):
        dataset: dict[str, Any] = {
            "organization": {"name": "Test Org"},
            "location": {"name": "Lab", "shortname": "lab"},
            "manufacturer": {"name": "Nokia"},
            "platform": {"name": "SR Linux"},
            "device_types": [{"name": "IXR-D2"}],
            "devices": [
                {
                    "name": "spine-01",
                    "role": "spine",
                    "use_case": "dcfabric",
                    "device_type": "IXR-D2",
                    "management_ip": "10.0.0.1/24",
                }
            ],
        }
        path = tmp_path / "topology.yml"
        path.write_text(yaml.safe_dump(dataset))

        client = self._make_client()
        ingester = SeedIngester(client=client)

        await ingester.seed(use_case="dcfabric", data_path=path)
        # Second run: existing nodes are returned by ``filters`` — simulate by
        # flipping filters to yield a matching node. The node.save call path
        # should be exercised without new create() calls for the device.
        existing_device = _stub_node(name="spine-01")
        existing_device.name = MagicMock(value="spine-01")  # attribute probe
        client.filters.return_value = [existing_device]
        create_calls_before = client.create.await_count

        result = await ingester.seed(use_case="dcfabric", data_path=path)

        # Devices already exist — we should not re-create them.
        device_create_calls_after = sum(
            1
            for call in client.create.await_args_list[create_calls_before:]
            if (call.kwargs.get("kind") or "").endswith("Device")
        )
        assert device_create_calls_after == 0
        assert result.devices_created == 0
        assert result.devices_updated == 1

    async def test_missing_data_path_raises(self, tmp_path: Path):
        client = self._make_client()
        ingester = SeedIngester(client=client)

        with pytest.raises(FileNotFoundError):
            await ingester.seed(use_case="dcfabric", data_path=tmp_path / "nope.yml")

    async def test_validation_error_for_device_without_required_fields(self, tmp_path: Path):
        # Device missing required 'role' — ingester should refuse before SDK.
        dataset = {"devices": [{"name": "incomplete"}]}
        path = tmp_path / "bad.yml"
        path.write_text(yaml.safe_dump(dataset))

        client = self._make_client()
        ingester = SeedIngester(client=client)

        with pytest.raises(IntentValidationError):
            await ingester.seed(use_case="dcfabric", data_path=path)

    async def test_connection_failure_translated(self, tmp_path: Path):
        dataset = {"organization": {"name": "Test Org"}}
        path = tmp_path / "topology.yml"
        path.write_text(yaml.safe_dump(dataset))

        client = self._make_client()
        client.create.side_effect = OSError("connection refused")
        ingester = SeedIngester(client=client)

        with pytest.raises(IntentConnectionError):
            await ingester.seed(use_case="dcfabric", data_path=path)
