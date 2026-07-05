"""Unit tests for ConfigRenderer (T010)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from jinja2 import UndefinedError

from snapl_executor.gnmi.renderer import RENDER_ERROR_KEY, ConfigRenderer

pytestmark = pytest.mark.unit


class TestConfigRendererLoad:
    def test_loads_dcfabric_templates(self):
        r = ConfigRenderer(use_case="dcfabric")
        assert r.use_case == "dcfabric"

    def test_unknown_use_case_raises(self):
        with pytest.raises(FileNotFoundError):
            ConfigRenderer(use_case="nonexistent_use_case_xyz")


class TestConfigRendererInterfaces:
    def test_render_produces_interface_key(self, dcfabric_desired_state):
        r = ConfigRenderer(use_case="dcfabric")
        payload = r.render(dcfabric_desired_state)
        assert "interface" in payload

    def test_render_produces_correct_interface_count(self, dcfabric_desired_state):
        r = ConfigRenderer(use_case="dcfabric")
        payload = r.render(dcfabric_desired_state)
        # 2 fabric interfaces + 1 loopback (lo0 from system.j2)
        assert len(payload["interface"]) == 3

    def test_interface_has_name(self, dcfabric_desired_state):
        r = ConfigRenderer(use_case="dcfabric")
        payload = r.render(dcfabric_desired_state)
        names = [iface["name"] for iface in payload["interface"]]
        assert "ethernet-1/1" in names
        assert "ethernet-1/2" in names

    def test_interface_has_ip_prefix(self, dcfabric_desired_state):
        r = ConfigRenderer(use_case="dcfabric")
        payload = r.render(dcfabric_desired_state)
        iface = next(i for i in payload["interface"] if i["name"] == "ethernet-1/1")
        subif = iface["subinterface"][0]
        assert "ipv4" in subif
        address = subif["ipv4"]["address"][0]
        assert address["ip-prefix"] == "10.1.1.0/31"

    def test_empty_interfaces_renders_no_fabric_interfaces(self, dcfabric_desired_state):
        ds = dcfabric_desired_state.model_copy(update={"interfaces": []})
        r = ConfigRenderer(use_case="dcfabric")
        payload = r.render(ds)
        non_loopback = [i for i in payload["interface"] if i["name"] != "lo0"]
        assert non_loopback == []


class TestConfigRendererSystem:
    def test_render_produces_system_loopback(self, dcfabric_desired_state):
        r = ConfigRenderer(use_case="dcfabric")
        payload = r.render(dcfabric_desired_state)
        lo_list = [i for i in payload.get("interface", []) if i["name"] == "lo0"]
        assert lo_list, "loopback lo0 should be in interface list"

    def test_loopback_has_management_address(self, dcfabric_desired_state):
        r = ConfigRenderer(use_case="dcfabric")
        payload = r.render(dcfabric_desired_state)
        lo = next(i for i in payload["interface"] if i["name"] == "lo0")
        address = lo["subinterface"][0]["ipv4"]["address"][0]["ip-prefix"]
        assert address.startswith("10.0.0.1")


class TestConfigRendererRenderError:
    def test_missing_variable_returns_render_error(self, dcfabric_desired_state):
        """Undefined template variables must be caught — not raised."""
        r = ConfigRenderer(use_case="dcfabric")
        with patch.object(r, "render", side_effect=UndefinedError("missing_field")):
            result = r.render_safe(dcfabric_desired_state)
        assert RENDER_ERROR_KEY in result
        assert "missing_field" in result[RENDER_ERROR_KEY]

    def test_render_safe_returns_dict_on_success(self, dcfabric_desired_state):
        r = ConfigRenderer(use_case="dcfabric")
        result = r.render_safe(dcfabric_desired_state)
        assert "error" not in result or result.get("error") is None


class TestConfigRendererBgp:
    """The rendered BGP config must be valid against SR Linux's YANG model:
    router-id and an enabled address family are mandatory, and any peer-group
    referenced by a neighbor must be defined as a group."""

    def _bgp(self, desired_state):
        payload = ConfigRenderer(use_case="dcfabric").render(desired_state)
        return payload["network-instance"][0]["protocols"]["bgp"]

    def test_bgp_has_router_id(self, dcfabric_desired_state):
        bgp = self._bgp(dcfabric_desired_state)
        assert bgp["router-id"] == "10.0.0.1"

    def test_bgp_enables_session_address_family(self, dcfabric_desired_state):
        bgp = self._bgp(dcfabric_desired_state)
        afis = {af["afi-safi-name"]: af["admin-state"] for af in bgp["afi-safi"]}
        assert afis == {"ipv4-unicast": "enable"}

    def test_bgp_defines_referenced_peer_group(self, dcfabric_desired_state):
        bgp = self._bgp(dcfabric_desired_state)
        groups = {g["group-name"] for g in bgp["group"]}
        assert groups == {"underlay-ipv4"}

    def test_bgp_without_peer_group_renders_no_group_list(self, dcfabric_desired_state):
        dcfabric_desired_state.bgp_sessions[0].peer_group = None
        bgp = self._bgp(dcfabric_desired_state)
        assert "group" not in bgp
        assert "peer-group" not in bgp["neighbor"][0]

    def test_bgp_is_admin_enabled(self, dcfabric_desired_state):
        bgp = self._bgp(dcfabric_desired_state)
        assert bgp["admin-state"] == "enable"
