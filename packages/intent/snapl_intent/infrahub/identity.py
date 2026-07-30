"""Is this Infrahub *ours*? (Issue #107)

A port answering is not evidence the instance behind it belongs to snapl. On a
developer machine running sibling projects, several of them serve Infrahub, and
pointing at the wrong one is not a connection error — it is a successful
connection to somebody else's Source of Truth. That has already happened once:
a schema provision aimed at a well-known port reached a neighbouring project's
server and tried to load snapl's schema into it.

The signature of a foreign instance is **populated, but without snapl's
markers**. snapl extends ``DcimDevice`` with a ``use_case`` attribute, so:

===========================  ==========  ==========  =========
instance                     devices     use_case    verdict
===========================  ==========  ==========  =========
fresh, nothing provisioned   0           absent      OURS (yet to be seeded)
snapl, provisioned/seeded    any         present     OURS
another project's            > 0         absent      FOREIGN
===========================  ==========  ==========  =========

Deliberately conservative: anything unreadable is reported as unknown rather
than foreign, because a false "this is not yours" would block legitimate work
on a healthy instance.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

SNAPL_DEVICE_KIND = "DcimDevice"
SNAPL_MARKER_ATTRIBUTE = "use_case"


class SotIdentity(StrEnum):
    OURS = "ours"
    FOREIGN = "foreign"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class IdentityCheck:
    """The verdict, plus a sentence explaining it."""

    identity: SotIdentity
    detail: str

    @property
    def is_foreign(self) -> bool:
        return self.identity is SotIdentity.FOREIGN


def classify(*, has_marker: bool, device_count: int | None, address: str) -> IdentityCheck:
    """Classify an instance from the two facts that distinguish it.

    Args:
        has_marker: Whether ``DcimDevice`` exposes snapl's ``use_case``.
        device_count: Devices present, or None when it could not be read.
        address: The address probed, for the message.
    """
    if has_marker:
        return IdentityCheck(SotIdentity.OURS, f"{address} carries snapl's schema")
    if device_count is None:
        return IdentityCheck(SotIdentity.UNKNOWN, f"{address} could not be identified")
    if device_count > 0:
        return IdentityCheck(
            SotIdentity.FOREIGN,
            f"{address} has {device_count} devices but no snapl {SNAPL_MARKER_ATTRIBUTE!r} attribute — "
            "this looks like another project's Infrahub",
        )
    return IdentityCheck(SotIdentity.OURS, f"{address} is empty and unprovisioned")


async def identify(client, *, address: str, branch: str = "main") -> IdentityCheck:
    """Classify the Infrahub behind ``client``.

    Any failure to read yields UNKNOWN, never FOREIGN — see the module note on
    being conservative.
    """
    try:
        schema = await client.schema.all(branch=branch, refresh=True)
    except Exception:
        return IdentityCheck(SotIdentity.UNKNOWN, f"{address} schema could not be read")

    node = schema.get(SNAPL_DEVICE_KIND) if hasattr(schema, "get") else None
    if node is None:
        # Nothing provisioned at all — a fresh instance waiting for us.
        return classify(has_marker=False, device_count=0, address=address)

    attributes = getattr(node, "attributes", None) or []
    has_marker = any(getattr(attribute, "name", None) == SNAPL_MARKER_ATTRIBUTE for attribute in attributes)
    if has_marker:
        return classify(has_marker=True, device_count=None, address=address)

    try:
        devices = await client.all(kind=SNAPL_DEVICE_KIND, branch=branch)
        count: int | None = len(devices)
    except Exception:
        count = None

    return classify(has_marker=False, device_count=count, address=address)
