"""One translation of Infrahub SDK failures into domain errors, for the store and the seed (#62).

The seed had its own copy of the store's rule; spec 017 T014 asks for one rule.
"""

from __future__ import annotations

import pytest

from snapl_intent.exceptions import IntentConnectionError, IntentValidationError
from snapl_intent.infrahub.sdk_errors import sdk_errors_as_domain_errors

pytestmark = pytest.mark.unit


class _SdkError(Exception):
    """Stands in for an exception raised from inside the Infrahub SDK."""


_SdkError.__module__ = "infrahub_sdk.exceptions"


@pytest.mark.parametrize("error", [OSError("connection refused"), TimeoutError("timed out")])
def test_a_lost_connection_is_an_unreachable_sot(error: Exception) -> None:
    unreachable = pytest.raises(IntentConnectionError, match=f"Infrahub unreachable: {error}")
    with unreachable as raised, sdk_errors_as_domain_errors():
        raise error

    assert raised.value.__cause__ is error


def test_an_error_from_inside_the_sdk_is_an_infrahub_error() -> None:
    error = _SdkError("graphql error")

    with (
        pytest.raises(IntentConnectionError, match="Infrahub error: graphql error") as raised,
        sdk_errors_as_domain_errors(),
    ):
        raise error

    assert raised.value.__cause__ is error


def test_a_domain_error_passes_unchanged() -> None:
    error = IntentValidationError("bad seed", field="local_ip")

    with pytest.raises(IntentValidationError) as raised, sdk_errors_as_domain_errors():
        raise error

    assert raised.value is error


def test_any_other_error_is_not_hidden() -> None:
    """A bug in snapl's own code must not read as an unreachable SoT."""
    with pytest.raises(KeyError), sdk_errors_as_domain_errors():
        raise KeyError("local_device")


def test_nothing_happens_when_nothing_fails() -> None:
    with sdk_errors_as_domain_errors():
        pass
