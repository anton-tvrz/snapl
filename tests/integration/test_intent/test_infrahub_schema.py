"""T027 — Integration test for schema provisioning against live Infrahub.

Validates that:
- The 3-batch schema load completes successfully end-to-end.
- Re-running ``provision_schema`` on an already-provisioned Infrahub is
  idempotent (no error, schemas_loaded count stable).

Fixtures come from ``tests/integration/test_intent/conftest.py``. Tests are
skipped if Infrahub is unreachable.
"""

from __future__ import annotations

import pytest

from snapl_intent.models import ProvisionResult

pytestmark = [pytest.mark.integration, pytest.mark.live]


async def test_provision_schema_loads_dcfabric(live_store) -> None:
    result = await live_store.provision_schema("dcfabric")

    assert isinstance(result, ProvisionResult)
    assert result.use_case == "dcfabric"
    assert result.schemas_loaded >= 1


async def test_provision_schema_is_idempotent(live_store) -> None:
    """Running provision twice must not error — the second call is a no-op."""
    first = await live_store.provision_schema("dcfabric")
    second = await live_store.provision_schema("dcfabric")

    assert first.schemas_loaded == second.schemas_loaded


# ---------------------------------------------------------------------------
# T039 — get_schema integration tests
# ---------------------------------------------------------------------------


async def test_get_schema_returns_schema_object(provisioned_store) -> None:
    from snapl_intent.models import Schema

    result = await provisioned_store.get_schema("dcfabric")

    assert isinstance(result, Schema)
    assert result.use_case == "dcfabric"


async def test_get_schema_includes_project_kinds(provisioned_store) -> None:
    result = await provisioned_store.get_schema("dcfabric")

    assert "DcimDevice" in result.entities
    assert "RoutingBGPSession" in result.entities
    assert "IpamPrefix" in result.entities


async def test_get_schema_excludes_infrahub_builtins(provisioned_store) -> None:
    result = await provisioned_store.get_schema("dcfabric")

    for entity in result.entities:
        assert not entity.startswith("Builtin"), f"Built-in kind leaked: {entity}"
        assert not entity.startswith("Core"), f"Core kind leaked: {entity}"


async def test_get_schema_has_source_files(provisioned_store) -> None:
    result = await provisioned_store.get_schema("dcfabric")

    assert result.source_files, "expected non-empty source_files"
    assert all(f.endswith(".yml") for f in result.source_files)


async def test_get_schema_raises_for_unknown_use_case(provisioned_store) -> None:
    from snapl_intent.exceptions import IntentSchemaError

    with pytest.raises(IntentSchemaError):
        await provisioned_store.get_schema("definitely-not-a-real-use-case")
