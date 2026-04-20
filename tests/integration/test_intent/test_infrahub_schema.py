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
