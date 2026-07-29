"""Source-of-Truth identity checks (issue #107).

A port answering is not evidence the instance is ours. These tests pin the
three-way classification and, in particular, its conservatism: an instance we
cannot read must never be called foreign, because a false "not yours" would
block legitimate work on a healthy server.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from snapl_intent.infrahub.identity import SotIdentity, classify, identify

pytestmark = pytest.mark.unit

ADDRESS = "http://localhost:18000"


class TestClassify:
    def test_snapl_marker_means_ours(self) -> None:
        result = classify(has_marker=True, device_count=99, address=ADDRESS)
        assert result.identity is SotIdentity.OURS
        assert not result.is_foreign

    def test_empty_and_unprovisioned_is_ours(self) -> None:
        """A fresh snapl instance has no marker yet — that is what seeding is
        for, and refusing here would make bootstrap impossible."""
        assert classify(has_marker=False, device_count=0, address=ADDRESS).identity is SotIdentity.OURS

    def test_populated_without_the_marker_is_foreign(self) -> None:
        """The #107 signature: somebody else's fabric."""
        result = classify(has_marker=False, device_count=3, address=ADDRESS)
        assert result.identity is SotIdentity.FOREIGN
        assert result.is_foreign
        assert "3 devices" in result.detail
        assert "another project" in result.detail

    def test_unreadable_is_unknown_not_foreign(self) -> None:
        result = classify(has_marker=False, device_count=None, address=ADDRESS)
        assert result.identity is SotIdentity.UNKNOWN
        assert not result.is_foreign, "a false 'not yours' must never block a healthy instance"


def _client(*, attributes: list[str] | None, devices: int | None = 0, schema_raises: bool = False):
    client = MagicMock()
    if schema_raises:
        client.schema.all = AsyncMock(side_effect=RuntimeError("connection reset"))
        return client
    if attributes is None:
        client.schema.all = AsyncMock(return_value={})
    else:
        node = SimpleNamespace(attributes=[SimpleNamespace(name=name) for name in attributes])
        client.schema.all = AsyncMock(return_value={"DcimDevice": node})
    client.all = AsyncMock(side_effect=RuntimeError("no")) if devices is None else AsyncMock(return_value=[1] * devices)
    return client


class TestIdentify:
    async def test_snapl_schema_is_recognised(self) -> None:
        client = _client(attributes=["name", "use_case", "lab_node_name"])
        assert (await identify(client, address=ADDRESS)).identity is SotIdentity.OURS

    async def test_no_device_kind_at_all_is_ours(self) -> None:
        """Nothing provisioned yet — a fresh instance waiting for us."""
        assert (await identify(client=_client(attributes=None), address=ADDRESS)).identity is SotIdentity.OURS

    async def test_foreign_fabric_is_detected(self) -> None:
        client = _client(attributes=["name", "role", "serial"], devices=3)
        result = await identify(client, address=ADDRESS)
        assert result.identity is SotIdentity.FOREIGN
        assert "3 devices" in result.detail

    async def test_foreign_schema_but_no_devices_is_ours(self) -> None:
        """A provisioned-but-empty instance is safe to seed into."""
        client = _client(attributes=["name", "role"], devices=0)
        assert (await identify(client, address=ADDRESS)).identity is SotIdentity.OURS

    async def test_unreadable_schema_is_unknown(self) -> None:
        client = _client(attributes=None, schema_raises=True)
        assert (await identify(client, address=ADDRESS)).identity is SotIdentity.UNKNOWN

    async def test_uncountable_devices_is_unknown(self) -> None:
        client = _client(attributes=["name"], devices=None)
        assert (await identify(client, address=ADDRESS)).identity is SotIdentity.UNKNOWN
