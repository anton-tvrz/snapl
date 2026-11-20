"""Infrahub SDK failures as domain errors: the store's and the seed's one rule (#62).

Callers of the Intent block see :class:`IntentError` subclasses only, never a
raw socket or SDK exception. A failure that comes from neither, a bug in
snapl's own code, is re-raised as it is rather than disguised as an
unreachable Source of Truth.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from snapl_intent.exceptions import IntentConnectionError, IntentError

if TYPE_CHECKING:
    from collections.abc import Iterator


@contextmanager
def sdk_errors_as_domain_errors() -> Iterator[None]:
    """Translate what the block's Infrahub calls raise.

    - a domain error passes unchanged;
    - ``OSError`` (``TimeoutError`` among them) is an unreachable SoT;
    - an exception defined inside the Infrahub SDK is an Infrahub error;
    - anything else is re-raised.
    """
    try:
        yield
    except IntentError:
        raise
    except OSError as exc:
        raise IntentConnectionError(f"Infrahub unreachable: {exc}") from exc
    except Exception as exc:
        if exc.__class__.__module__.startswith("infrahub"):
            raise IntentConnectionError(f"Infrahub error: {exc}") from exc
        raise
