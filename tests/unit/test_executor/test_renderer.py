"""Unit tests for ConfigRenderer (T010)."""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest
from jinja2 import TemplateError, UndefinedError

from snapl_executor.gnmi.renderer import RENDER_ERROR_KEY, ConfigRenderer

pytestmark = pytest.mark.unit


def _iface(**overrides):
    from snapl_intent.models import Interface

    defaults = {
        "id": uuid4(),
        "device_id": uuid4(),
        "name": "ethernet-1/10",
        "enabled": True,
    }
    return Interface(**{**defaults, **overrides})


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
        # Exactly the intent's interfaces — no synthetic loopback (#78).
        assert len(payload["interface"]) == 2

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

    def test_empty_interfaces_renders_empty_interface_list(self, dcfabric_desired_state):
        ds = dcfabric_desired_state.model_copy(update={"interfaces": []})
        r = ConfigRenderer(use_case="dcfabric")
        payload = r.render(ds)
        assert payload["interface"] == []


class TestConfigRendererEntityAndFieldCoverage:
    """Every intent interface is rendered (#64) with every diff-compared field (#59)."""

    def test_ip_less_interface_is_rendered(self, dcfabric_desired_state):
        ds = dcfabric_desired_state.model_copy(update={"interfaces": [_iface(name="ethernet-1/10")]})
        payload = ConfigRenderer(use_case="dcfabric").render(ds)
        entry = next(i for i in payload["interface"] if i["name"] == "ethernet-1/10")
        assert entry["admin-state"] == "enable"
        assert "subinterface" not in entry

    def test_disabled_ip_less_interface_renders_shutdown(self, dcfabric_desired_state):
        ds = dcfabric_desired_state.model_copy(update={"interfaces": [_iface(enabled=False)]})
        payload = ConfigRenderer(use_case="dcfabric").render(ds)
        entry = next(i for i in payload["interface"] if i["name"] == "ethernet-1/10")
        assert entry["admin-state"] == "disable"

    def test_interface_mtu_is_rendered_when_set(self, dcfabric_desired_state):
        payload = ConfigRenderer(use_case="dcfabric").render(dcfabric_desired_state)
        entry = next(i for i in payload["interface"] if i["name"] == "ethernet-1/1")
        assert entry["mtu"] == 9232

    def test_interface_mtu_omitted_when_unset(self, dcfabric_desired_state):
        """SR Linux rejects mtu on loopbacks — mtu-less intent must not render one (#78)."""
        ds = dcfabric_desired_state.model_copy(
            update={"interfaces": [_iface(name="lo0", ip_address="10.1.0.1", prefix_length=32, mtu=None)]},
        )
        payload = ConfigRenderer(use_case="dcfabric").render(ds)
        entry = next(i for i in payload["interface"] if i["name"] == "lo0")
        assert "mtu" not in entry

    def test_interface_description_is_rendered_when_set(self, dcfabric_desired_state):
        ds = dcfabric_desired_state.model_copy(
            update={"interfaces": [_iface(description='to leaf-01 "uplink"')]},
        )
        payload = ConfigRenderer(use_case="dcfabric").render(ds)
        entry = next(i for i in payload["interface"] if i["name"] == "ethernet-1/10")
        assert entry["description"] == 'to leaf-01 "uplink"'

    def test_interface_description_omitted_when_unset(self, dcfabric_desired_state):
        payload = ConfigRenderer(use_case="dcfabric").render(dcfabric_desired_state)
        entry = next(i for i in payload["interface"] if i["name"] == "ethernet-1/1")
        assert "description" not in entry


class TestConfigRendererPrefixValidation:
    """An IP without a prefix length is a render error, not an invalid SET (#72)."""

    def test_ip_without_prefix_length_raises(self, dcfabric_desired_state):
        ds = dcfabric_desired_state.model_copy(
            update={"interfaces": [_iface(ip_address="10.10.1.0", prefix_length=None)]},
        )
        r = ConfigRenderer(use_case="dcfabric")
        with pytest.raises(TemplateError, match="ethernet-1/10"):
            r.render(ds)

    def test_render_safe_reports_missing_prefix_as_render_error(self, dcfabric_desired_state):
        ds = dcfabric_desired_state.model_copy(
            update={"interfaces": [_iface(ip_address="10.10.1.0", prefix_length=None)]},
        )
        result = ConfigRenderer(use_case="dcfabric").render_safe(ds)
        assert RENDER_ERROR_KEY in result
        assert "ethernet-1/10" in result[RENDER_ERROR_KEY]


class TestConfigRendererLoopback:
    """The seeded lo0 is *the* loopback — no synthetic system.j2 injection (#78).

    Intent-first (Constitution VIII): every rendered entity comes from the
    DesiredState. A hardcoded lo0 collided with seeded loopback rows and
    carried the management address instead of the intended router-id."""

    def test_no_loopback_is_injected_without_intent(self, dcfabric_desired_state):
        payload = ConfigRenderer(use_case="dcfabric").render(dcfabric_desired_state)
        assert [i for i in payload["interface"] if i["name"] == "lo0"] == []

    def test_seeded_loopback_renders_from_intent(self, dcfabric_desired_state):
        ds = dcfabric_desired_state.model_copy(
            update={"interfaces": [_iface(name="lo0", ip_address="10.1.0.1", prefix_length=32, mtu=None)]},
        )
        payload = ConfigRenderer(use_case="dcfabric").render(ds)
        (lo,) = [i for i in payload["interface"] if i["name"] == "lo0"]
        address = lo["subinterface"][0]["ipv4"]["address"][0]["ip-prefix"]
        assert address == "10.1.0.1/32"


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

    def test_bgp_router_id_from_loopback(self, dcfabric_desired_state):
        """router-id derives from the seeded loopback address, not the
        management IP — the seed designates lo0's IP as the router ID and
        management IPs are intent-only, never live on the device (#83)."""
        ds = dcfabric_desired_state.model_copy(
            update={
                "interfaces": [
                    *dcfabric_desired_state.interfaces,
                    _iface(name="lo0", ip_address="10.1.0.5", prefix_length=32, mtu=None),
                ]
            }
        )
        bgp = self._bgp(ds)
        assert bgp["router-id"] == "10.1.0.5"

    def test_bgp_router_id_falls_back_to_management_without_loopback(self, dcfabric_desired_state):
        """No loopback carries an address in intent → fall back to
        management_address so a unique router-id still renders (#83)."""
        bgp = self._bgp(dcfabric_desired_state)  # fixture has no loopback
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


class TestConfigRendererDefaultInstance:
    """An address is routed only when its subinterface belongs to a network
    instance, so every addressed interface joins ``default`` (#315). Without it,
    ``ping`` over a configured /31 says "Network is unreachable" and no session
    ever comes up."""

    def _instance(self, desired_state):
        payload = ConfigRenderer(use_case="dcfabric").render(desired_state)
        instances = payload["network-instance"]
        assert [ni["name"] for ni in instances] == ["default"]
        return instances[0]

    def test_every_addressed_interface_joins_the_default_instance(self, dcfabric_desired_state):
        addressed = [i.name for i in dcfabric_desired_state.interfaces if i.ip_address]
        assert addressed, "fixture has no addressed interface"

        instance = self._instance(dcfabric_desired_state)

        assert instance["interface"] == [{"name": f"{name}.0"} for name in addressed]

    def test_an_interface_without_an_address_does_not_join(self, dcfabric_desired_state):
        dcfabric_desired_state.interfaces.append(_iface(name="ethernet-1/10"))

        names = [entry["name"] for entry in self._instance(dcfabric_desired_state)["interface"]]

        assert "ethernet-1/10.0" not in names

    def test_a_device_without_sessions_still_routes_its_addresses(self, dcfabric_desired_state):
        dcfabric_desired_state.bgp_sessions.clear()

        instance = self._instance(dcfabric_desired_state)

        assert instance["interface"]
        assert "protocols" not in instance

    def test_a_device_with_no_address_and_no_session_renders_no_instance(self, dcfabric_desired_state):
        dcfabric_desired_state.bgp_sessions.clear()
        dcfabric_desired_state.interfaces[:] = [_iface(name="ethernet-1/10")]

        payload = ConfigRenderer(use_case="dcfabric").render(dcfabric_desired_state)

        assert "network-instance" not in payload


class TestConfigRendererRoutingPolicy:
    """A session with no policy gets SR Linux's eBGP default, which advertises
    and accepts nothing (#317). The policy is intent, rendered as SR Linux's
    prefix-set and policy, and named on each neighbour. The shapes below are the
    ones SR Linux v26.3.1 validated and reported back (2026-11-20)."""

    @staticmethod
    def _with_policy(desired_state, **policy):
        from snapl_intent.models import RoutingPolicy

        defaults = {"name": "underlay-loopbacks", "accept_prefixes": ["10.1.0.0/24 32..32"]}
        desired_state.routing_policies.append(RoutingPolicy(**{**defaults, **policy}))
        for session in desired_state.bgp_sessions:
            session.export_policy = session.import_policy = "underlay-loopbacks"
        return desired_state

    def test_the_policy_renders_as_a_prefix_set_and_a_policy(self, dcfabric_desired_state):
        payload = ConfigRenderer(use_case="dcfabric").render(self._with_policy(dcfabric_desired_state))

        assert payload["routing-policy"] == {
            "prefix-set": [
                {
                    "name": "underlay-loopbacks",
                    "prefix": [{"ip-prefix": "10.1.0.0/24", "mask-length-range": "32..32"}],
                }
            ],
            "policy": [
                {
                    "name": "underlay-loopbacks",
                    "default-action": {"policy-result": "reject"},
                    "statement": [
                        {
                            "name": "accept",
                            "match": {"prefix": {"prefix-set": "underlay-loopbacks"}},
                            "action": {"policy-result": "accept"},
                        }
                    ],
                }
            ],
        }

    def test_a_policy_that_lists_nothing_renders_its_default_action_alone(self, dcfabric_desired_state):
        desired = self._with_policy(dcfabric_desired_state, accept_prefixes=[], default_action="accept")

        payload = ConfigRenderer(use_case="dcfabric").render(desired)

        assert payload["routing-policy"] == {
            "policy": [{"name": "underlay-loopbacks", "default-action": {"policy-result": "accept"}}]
        }

    def test_each_neighbour_names_its_policies(self, dcfabric_desired_state):
        payload = ConfigRenderer(use_case="dcfabric").render(self._with_policy(dcfabric_desired_state))

        (neighbor,) = payload["network-instance"][0]["protocols"]["bgp"]["neighbor"]
        assert neighbor["export-policy"] == ["underlay-loopbacks"]
        assert neighbor["import-policy"] == ["underlay-loopbacks"]

    def test_a_neighbour_without_policies_names_none(self, dcfabric_desired_state):
        payload = ConfigRenderer(use_case="dcfabric").render(dcfabric_desired_state)

        (neighbor,) = payload["network-instance"][0]["protocols"]["bgp"]["neighbor"]
        assert "export-policy" not in neighbor
        assert "import-policy" not in neighbor
        assert "routing-policy" not in payload

    @pytest.mark.parametrize("field", ["export_policy", "import_policy"])
    def test_a_session_naming_a_policy_intent_does_not_hold_is_refused(self, dcfabric_desired_state, field):
        """The device refuses the whole SET over a neighbour naming a policy it
        does not have; say which name, before anything is sent."""
        setattr(dcfabric_desired_state.bgp_sessions[0], field, "nowhere")

        with pytest.raises(TemplateError, match="nowhere"):
            ConfigRenderer(use_case="dcfabric").render(dcfabric_desired_state)

        assert "nowhere" in ConfigRenderer(use_case="dcfabric").render_safe(dcfabric_desired_state)[RENDER_ERROR_KEY]
