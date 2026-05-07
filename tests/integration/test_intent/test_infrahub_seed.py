"""T028 — Integration test for data seeding against live Infrahub.

Validates that:
- The packaged dcfabric topology seeds end-to-end into Infrahub.
- Re-running ``seed`` is an upsert — the device count stays stable and
  ``devices_created`` drops to zero (or stays bounded) on the second run.

Fixtures come from ``tests/integration/test_intent/conftest.py``. Tests are
skipped if Infrahub is unreachable or schema provisioning fails.
"""

from __future__ import annotations

import pytest

from snapl_intent.models import SeedResult

pytestmark = [pytest.mark.integration, pytest.mark.live]


async def test_seed_dcfabric_topology(provisioned_store) -> None:
    result = await provisioned_store.seed("dcfabric")

    assert isinstance(result, SeedResult)
    assert result.use_case == "dcfabric"
    assert result.total_records > 0


async def test_seed_is_idempotent(provisioned_store) -> None:
    """A second seed run must not create duplicate devices."""
    first = await provisioned_store.seed("dcfabric")
    second = await provisioned_store.seed("dcfabric")

    assert second.devices_created == 0
    assert second.total_records == first.total_records
