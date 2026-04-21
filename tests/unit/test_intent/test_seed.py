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
    SEED_DEFERRED,
    SEED_ORDER,
    SeedIngester,
    load_seed_file,
)

# Full dependency order = SEED_ORDER plus remaining deferred sections
# (IP-namespace, interfaces, RoutingProtocol shadow copies — see
# T028-followup). Invariants below cover the whole chain so they remain
# meaningful as deferred sections graduate into SEED_ORDER.
FULL_ORDER: list[str] = SEED_ORDER + SEED_DEFERRED

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
        assert FULL_ORDER.index("organization") < FULL_ORDER.index("devices")
        assert FULL_ORDER.index("manufacturer") < FULL_ORDER.index("devices")
        assert FULL_ORDER.index("platform") < FULL_ORDER.index("devices")
        assert FULL_ORDER.index("device_types") < FULL_ORDER.index("devices")
        assert FULL_ORDER.index("location") < FULL_ORDER.index("devices")
        assert FULL_ORDER.index("autonomous_systems") < FULL_ORDER.index("devices")

    def test_interfaces_come_after_devices(self):
        assert FULL_ORDER.index("devices") < FULL_ORDER.index("interfaces")

    def test_bgp_sessions_come_last(self):
        assert FULL_ORDER.index("bgp_peer_groups") < FULL_ORDER.index("bgp_sessions")
        assert FULL_ORDER.index("interfaces") < FULL_ORDER.index("bgp_sessions")

    def test_active_and_deferred_are_disjoint(self):
        assert set(SEED_ORDER).isdisjoint(SEED_DEFERRED)


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
        # Attribute-only sections — no relationship resolution needed, so the
        # default ``filters=[]`` mock is sufficient. Relationship resolution is
        # covered by ``test_ingest_resolves_relationships_to_peer_ids`` below.
        dataset: dict[str, Any] = {
            "organization": {"name": "Test Org"},
            "location": {"name": "Lab", "shortname": "lab"},
            "manufacturer": {"name": "Nokia"},
            "platform": {"name": "SR Linux"},
        }
        path = tmp_path / "topology.yml"
        path.write_text(yaml.safe_dump(dataset))

        client = self._make_client()
        ingester = SeedIngester(client=client)

        result = await ingester.seed(use_case="dcfabric", data_path=path, branch="main")

        assert client.create.await_count == len(dataset)
        create_kinds = [call.kwargs.get("kind") for call in client.create.await_args_list]
        assert any("Organization" in (k or "") for k in create_kinds)
        assert result.use_case == "dcfabric"
        assert result.branch == "main"
        assert result.devices_created == 0
        assert result.total_records == len(dataset)

    async def test_ingest_resolves_relationships_to_peer_ids(self, tmp_path: Path):
        dataset: dict[str, Any] = {
            "device_types": [
                {"name": "IXR-D2", "manufacturer": "Nokia"},
            ],
        }
        path = tmp_path / "topology.yml"
        path.write_text(yaml.safe_dump(dataset))

        peer = _stub_node()
        peer.id = "mfr-1"

        async def fake_filters(*, kind: str, **kwargs: Any) -> list[Any]:
            if kind == "OrganizationManufacturer" and kwargs.get("name__value") == "Nokia":
                return [peer]
            return []

        client = self._make_client()
        client.filters = AsyncMock(side_effect=fake_filters)
        ingester = SeedIngester(client=client)

        await ingester.seed(use_case="dcfabric", data_path=path)

        device_type_call = next(
            call
            for call in client.create.await_args_list
            if call.kwargs.get("kind") == "DcimDeviceType"
        )
        assert device_type_call.kwargs["data"]["manufacturer"] == "mfr-1"

    async def test_ingest_raises_when_relationship_peer_missing(self, tmp_path: Path):
        dataset: dict[str, Any] = {
            "device_types": [
                {"name": "IXR-D2", "manufacturer": "Unknown"},
            ],
        }
        path = tmp_path / "topology.yml"
        path.write_text(yaml.safe_dump(dataset))

        client = self._make_client()
        ingester = SeedIngester(client=client)

        with pytest.raises(IntentValidationError, match="Unresolved manufacturer"):
            await ingester.seed(use_case="dcfabric", data_path=path)

    async def test_ingest_second_run_upserts_in_place(self, tmp_path: Path):
        dataset: dict[str, Any] = {
            "organization": {"name": "Test Org"},
            "location": {"name": "Lab", "shortname": "lab"},
            "manufacturer": {"name": "Nokia"},
            "platform": {"name": "SR Linux"},
        }
        path = tmp_path / "topology.yml"
        path.write_text(yaml.safe_dump(dataset))

        client = self._make_client()
        ingester = SeedIngester(client=client)

        await ingester.seed(use_case="dcfabric", data_path=path)
        create_calls_before = client.create.await_count

        # Second run: ``filters`` now returns an existing node so the ingester
        # takes the update branch instead of calling ``create``.
        existing = _stub_node(name="existing")
        existing.name = MagicMock(value="Test Org")
        client.filters.return_value = [existing]

        result = await ingester.seed(use_case="dcfabric", data_path=path)

        # No new create calls after the second seed — everything was an upsert.
        assert client.create.await_count == create_calls_before
        assert result.total_records == len(dataset)

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
