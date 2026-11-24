"""GnmiExecutor's removal: the delete-path guard (spec 013, T004).

The guard is the executor's own check that a delete names one whole entity of
a removable kind, and never a protected interface, whatever the plan says
(FR-004, FR-005). It runs before any dial.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

_PROTECTED = ("mgmt0", "system0")


def _check(paths, protected=_PROTECTED):
    from snapl_executor.gnmi.executor import check_delete_paths

    return check_delete_paths(paths, protected)


_ACCEPTED = [
    "/interface[name=ethernet-1/7]",
    "/interface[name=lo1]",
    "/interface[name=ethernet-1/49]/subinterface[index=0]/ipv4/address[ip-prefix=192.0.2.1/31]",
    "/interface[name=ethernet-1/7]/subinterface[index=12]/ipv4/address[ip-prefix=10.0.0.0/8]",
    "/network-instance[name=default]/protocols/bgp/neighbor[peer-address=192.0.2.0]",
    "/network-instance[name=default]/interface[name=ethernet-1/7.0]",
    "/network-instance[name=default]/interface[name=lo0.0]",
]

_REFUSED = [
    "/",
    "",
    "/interface",
    "/interface[name=]",
    "/network-instance[name=default]",
    "/network-instance[name=default]/protocols/bgp",
    "/network-instance[name=default]/protocols/bgp/group[group-name=c-probe-group]",
    "/network-instance[name=default]/interface",
    "/network-instance[name=mgmt]/interface[name=ethernet-1/7.0]",
    "/network-instance[name=mgmt]/protocols/bgp/neighbor[peer-address=192.0.2.0]",
    "/routing-policy",
    "/routing-policy/policy[name=underlay-loopbacks]",
    "/interface[name=ethernet-1/7]/description",
    "/interface[name=ethernet-1/7]/subinterface[index=0]",
    "/interface[name=ethernet-1/7]/subinterface[index=0]/ipv4",
    "/interface[name=ethernet-1/7]/subinterface[index=x]/ipv4/address[ip-prefix=192.0.2.1/31]",
    "/network-instance[name=default]/protocols/bgp/neighbor[peer-address=192.0.2.0]/peer-as",
    "/network-instance[name=default]/interface[name=ethernet-1/7]",
    "/interface[name=ethernet-1/7] ",
    "/interface[name=ethernet-1/7]/",
    "/interface[name=ethernet-1/7]x",
    "interface[name=ethernet-1/7]",
    "/interface[name=a]/subinterface[index=0]/x[name=b]",
]


class TestShapes:
    @pytest.mark.parametrize("path", _ACCEPTED)
    def test_an_entity_shape_is_accepted(self, path):
        assert _check([path]) == {}

    @pytest.mark.parametrize("path", _REFUSED)
    def test_anything_else_is_refused(self, path):
        refused = _check([path])
        assert list(refused) == [path]
        assert refused[path] != "protected"

    def test_only_the_refused_paths_are_returned(self):
        bad = "/interface[name=ethernet-1/7]/description"
        assert list(_check([*_ACCEPTED, bad])) == [bad]


class TestProtected:
    @pytest.mark.parametrize(
        "path",
        [
            "/interface[name=mgmt0]",
            "/interface[name=system0]",
            "/interface[name=mgmt0]/subinterface[index=0]/ipv4/address[ip-prefix=172.20.21.14/24]",
            "/network-instance[name=default]/interface[name=system0.0]",
            "/network-instance[name=default]/interface[name=mgmt0.0]",
        ],
    )
    def test_a_protected_interface_is_refused_as_protected(self, path):
        assert _check([path]) == {path: "protected"}

    def test_a_name_that_merely_starts_like_a_protected_one_is_not_protected(self):
        assert _check(["/interface[name=mgmt01]", "/network-instance[name=default]/interface[name=system01.0]"]) == {}

    def test_no_protected_set_refuses_everything(self):
        """Fail closed: an executor built without the field map's set deletes nothing (R5)."""
        refused = _check(_ACCEPTED, protected=None)
        assert set(refused) == set(_ACCEPTED)
        assert set(refused.values()) == {"no protected set configured"}


# ---------------------------------------------------------------------------
# dry_run_remove (T014)
# ---------------------------------------------------------------------------

_PORT = "/interface[name=ethernet-1/7]"
_NEIGHBOR = "/network-instance[name=default]/protocols/bgp/neighbor[peer-address=192.0.2.0]"


def _executor(**kwargs):
    from snapl_executor.gnmi.executor import GnmiExecutor

    return GnmiExecutor(password="test", **kwargs)  # pragma: allowlist secret


@pytest.fixture
def no_dial():
    """pygnmi's client, patched to fail the test if anything constructs it."""
    from unittest.mock import patch

    with patch("snapl_executor.gnmi.executor.gNMIclient", side_effect=AssertionError("dialled")) as client:
        yield client


class TestDryRunRemove:
    async def test_allowed_paths_pass_without_dialling(self, make_device, no_dial):
        device = make_device("leaf-04")
        result = await _executor(protected_interfaces=_PROTECTED).dry_run_remove(device, [_PORT, _NEIGHBOR])

        assert result.success is True
        assert result.dry_run is True
        assert result.paths == [_PORT, _NEIGHBOR]
        assert result.refused == {}
        assert result.device_response is None
        assert result.device_id == device.id
        assert result.device_name == "leaf-04"
        no_dial.assert_not_called()

    async def test_a_refused_path_fails_the_dry_run(self, make_device, no_dial):
        protected = "/interface[name=mgmt0]"
        result = await _executor(protected_interfaces=_PROTECTED).dry_run_remove(
            make_device("leaf-04"), [_PORT, protected]
        )

        assert result.success is False
        assert result.refused == {protected: "protected"}
        assert protected in result.error
        no_dial.assert_not_called()

    async def test_without_a_protected_set_every_path_is_refused(self, make_device, no_dial):
        result = await _executor().dry_run_remove(make_device("leaf-04"), [_PORT, _NEIGHBOR])

        assert result.success is False
        assert set(result.refused) == {_PORT, _NEIGHBOR}
        assert "no protected set configured" in result.error

    @pytest.mark.parametrize("paths", [[], [_PORT, _PORT]], ids=["empty", "duplicate"])
    async def test_a_bad_path_list_is_a_programming_error(self, make_device, paths):
        with pytest.raises(ValueError, match="paths"):
            await _executor(protected_interfaces=_PROTECTED).dry_run_remove(make_device("leaf-04"), paths)


# ---------------------------------------------------------------------------
# remove (T027)
# ---------------------------------------------------------------------------

# C1 (captures.md): SR Linux's rejection of a Set that deletes a bound port
# without its binding. The whole Set is refused and the text names the path.
_C1_REJECTION = (
    'Error in path: .network-instance{.name=="default"}.interface{.name=="ethernet-1/7.0"}\n'
    "    [FailedPrecondition] subinterface ethernet-1/7.0 not found"
)
_BINDING = "/network-instance[name=default]/interface[name=ethernet-1/7.0]"


@pytest.fixture
def gnmi(mock_gnmi_client):
    """pygnmi's client class, patched to hand back the mock client."""
    from unittest.mock import patch

    with patch("snapl_executor.gnmi.executor.gNMIclient", return_value=mock_gnmi_client) as client:
        yield client


class TestRemove:
    async def test_one_set_of_deletes_only(self, make_device, gnmi, mock_gnmi_client):
        device = make_device("leaf-04", lab_node_name="clab-dcfabric-leaf-04")
        paths = [_PORT, _BINDING, _NEIGHBOR]
        result = await _executor(protected_interfaces=_PROTECTED).remove(device, paths)

        assert result.success is True
        assert result.dry_run is False
        assert result.paths == paths
        assert result.refused == {}
        assert result.error is None
        assert result.device_response is not None
        assert result.duration_ms >= 0
        gnmi.assert_called_once()
        assert gnmi.call_args.kwargs["target"] == ("clab-dcfabric-leaf-04", 57400)
        mock_gnmi_client.set.assert_called_once_with(delete=paths)

    async def test_a_refused_path_means_nothing_is_dialled(self, make_device, no_dial):
        protected = "/network-instance[name=default]/interface[name=system0.0]"
        result = await _executor(protected_interfaces=_PROTECTED).remove(make_device("leaf-04"), [_PORT, protected])

        assert result.success is False
        assert result.dry_run is False
        assert result.refused == {protected: "protected"}
        assert protected in result.error
        assert result.device_response is None
        no_dial.assert_not_called()

    async def test_without_a_protected_set_nothing_is_dialled(self, make_device, no_dial):
        result = await _executor().remove(make_device("leaf-04"), [_PORT])

        assert result.success is False
        assert result.refused == {_PORT: "no protected set configured"}
        no_dial.assert_not_called()

    async def test_a_rejection_keeps_the_device_text(self, make_device, gnmi, mock_gnmi_client):
        mock_gnmi_client.set.side_effect = Exception(_C1_REJECTION)
        result = await _executor(protected_interfaces=_PROTECTED).remove(make_device("leaf-04"), [_PORT])

        assert result.success is False
        assert result.error == _C1_REJECTION
        assert result.refused == {}
        assert result.device_response is None

    async def test_a_silent_dial_error_names_the_target(self, make_device):
        """#194: a dial that never connects raises an exception with no message."""
        from unittest.mock import patch

        class _SilentError(Exception):
            def __str__(self) -> str:
                return ""

        device = make_device("leaf-04", lab_node_name="clab-dcfabric-leaf-04")
        with patch("snapl_executor.gnmi.executor.gNMIclient") as client:
            client.return_value.__enter__.side_effect = _SilentError()
            result = await _executor(protected_interfaces=_PROTECTED).remove(device, [_PORT])

        assert result.success is False
        assert result.error == "_SilentError, dialing clab-dcfabric-leaf-04:57400"

    async def test_the_set_holds_the_hosts_lock(self, make_device, gnmi, mock_gnmi_client):
        """SR Linux runs one exclusive config session at a time, so a removal
        waits behind a deploy's Set to the same host."""
        executor = _executor(protected_interfaces=_PROTECTED)
        device = make_device("leaf-04", lab_node_name="clab-dcfabric-leaf-04")
        held: list[bool] = []
        mock_gnmi_client.set.side_effect = lambda **_: (
            held.append(executor._set_locks["clab-dcfabric-leaf-04"].locked()) or {"response": []}
        )

        result = await executor.remove(device, [_PORT])

        assert result.success is True
        assert held == [True]

    @pytest.mark.parametrize("paths", [[], [_PORT, _PORT]], ids=["empty", "duplicate"])
    async def test_a_bad_path_list_is_a_programming_error(self, make_device, no_dial, paths):
        with pytest.raises(ValueError, match="paths"):
            await _executor(protected_interfaces=_PROTECTED).remove(make_device("leaf-04"), paths)
