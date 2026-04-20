"""Unit tests for the 3-batch schema provisioning logic.

Covers discovery, ordering, idempotent load, and translation of SDK errors.
Tests run against a mock client — no Infrahub required.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from snapl_intent.exceptions import IntentConnectionError, IntentSchemaError
from snapl_intent.infrahub.schema import (
    BATCH_BASE,
    BATCH_EXTENSIONS,
    BATCH_PROJECT,
    SchemaLoader,
    discover_schema_batches,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class TestDiscoverSchemaBatches:
    def test_discovers_three_batches_in_order(self, intent_package_root: Path):
        batches = discover_schema_batches(intent_package_root / "schemas")

        assert len(batches) == 3
        base, extensions, project = batches

        assert all(p.parent.name == "base" for p in base), base
        assert all(p.parent.name == "extensions" for p in extensions), extensions
        assert all(p.parent.name == "schemas" for p in project), project

    def test_base_batch_contains_expected_files(self, intent_package_root: Path):
        base = discover_schema_batches(intent_package_root / "schemas")[BATCH_BASE]
        names = {p.name for p in base}

        assert names == {"dcim.yml", "ipam.yml", "location.yml", "organization.yml"}

    def test_project_batch_contains_expected_files(self, intent_package_root: Path):
        project = discover_schema_batches(intent_package_root / "schemas")[BATCH_PROJECT]
        names = {p.name for p in project}

        # Project-specific Batch 3 — excludes README.md and non-yml files.
        assert "network_device.yml" in names
        assert "network_interface.yml" in names
        assert "business_intent.yml" in names
        assert all(p.suffix == ".yml" for p in project)

    def test_batches_are_lexicographically_sorted(self, intent_package_root: Path):
        base = discover_schema_batches(intent_package_root / "schemas")[BATCH_BASE]
        assert [p.name for p in base] == sorted(p.name for p in base)

    def test_missing_directory_raises(self, tmp_path: Path):
        with pytest.raises(IntentSchemaError):
            discover_schema_batches(tmp_path / "does-not-exist")


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


class TestSchemaLoader:
    def _make_client(self) -> MagicMock:
        client = MagicMock()
        client.schema = MagicMock()
        client.schema.load = AsyncMock(return_value={"errors": []})
        return client

    async def test_load_calls_client_three_times_in_order(self, intent_package_root: Path):
        client = self._make_client()
        loader = SchemaLoader(client=client, schemas_root=intent_package_root / "schemas")

        result = await loader.load()

        # One call per non-empty batch.
        assert client.schema.load.await_count == 3
        # Batches sized as expected.
        assert result.schemas_loaded >= 7  # 4 base + 3 extensions + >=3 project

    async def test_load_aggregates_schema_count(self, intent_package_root: Path):
        client = self._make_client()
        loader = SchemaLoader(client=client, schemas_root=intent_package_root / "schemas")

        result = await loader.load()

        assert result.use_case == "dcfabric"
        assert result.schemas_loaded == 4 + 3 + 3  # base + extensions + project

    async def test_load_raises_schema_error_on_validation_failure(
        self, intent_package_root: Path
    ):
        client = self._make_client()
        client.schema.load.return_value = {"errors": [{"message": "bad kind"}]}
        loader = SchemaLoader(client=client, schemas_root=intent_package_root / "schemas")

        with pytest.raises(IntentSchemaError) as exc_info:
            await loader.load()

        assert "bad kind" in str(exc_info.value)

    async def test_load_translates_connection_error(self, intent_package_root: Path):
        client = self._make_client()
        client.schema.load.side_effect = OSError("connection refused")
        loader = SchemaLoader(client=client, schemas_root=intent_package_root / "schemas")

        with pytest.raises(IntentConnectionError):
            await loader.load()

    async def test_load_idempotent_reports_no_change(self, intent_package_root: Path):
        client = self._make_client()
        # Infrahub reports no diff — our wrapper should still succeed without
        # an exception being raised.
        client.schema.load.return_value = {"errors": []}
        loader = SchemaLoader(client=client, schemas_root=intent_package_root / "schemas")

        first = await loader.load()
        second = await loader.load()

        assert first.schemas_loaded == second.schemas_loaded
        assert client.schema.load.await_count == 6  # 3 batches × 2 invocations


# ---------------------------------------------------------------------------
# Batch indices
# ---------------------------------------------------------------------------


def test_batch_indices_are_sequential():
    assert BATCH_BASE == 0
    assert BATCH_EXTENSIONS == 1
    assert BATCH_PROJECT == 2
