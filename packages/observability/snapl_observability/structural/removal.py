"""The removal planner: a drift report's undesired items, as deletes (spec 013).

The diff run backwards. ``diff.py`` builds every undesired item's path from the
entity's ``path_template`` in ``ENTITY_FIELD_MAP``; this module takes those
paths apart again, so it sits in the same seam and reads the same map (research
R1). Each undesired entity gets one delete of its entry, and an extra address
on an interface intent manages gets a delete of exactly its own path (R2):

====================  ===============================================  ==========================
kind                  undesired item                                   delete
====================  ===============================================  ==========================
``interface``         ``/interface[name=X]/<field>``                   ``/interface[name=X]``
``interface``         ``/interface[name=X]/subinterface[index=N]/``    the item's path, verbatim
                      ``ipv4/address[ip-prefix=P]``
``bgp_session``       ``…/bgp/neighbor[peer-address=A]/<field>``       the neighbour entry
``routed_interface``  ``/network-instance[name=default]/``             the binding entry
                      ``interface[name=X.N]/name``
====================  ===============================================  ==========================

Anything else is refused as ``not_removable`` rather than guessed at, and an
interface in the field map's protected set (or a binding of one) is refused as
``protected``. Forward drift is never planned (FR-003).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from snapl_observability.models import (
    DriftStatus,
    PlannedDelete,
    RefusedEntity,
    RemovalPlan,
    RemovalRefusal,
)
from snapl_observability.structural.diff import ENTITY_FIELD_MAP, _key_pattern

if TYPE_CHECKING:
    from snapl_observability.models import DriftItem, DriftReport

# What follows an interface's entry in an extra address's path (#193). The
# collector's adapter writes these, not a path template, so there is no
# template to compile them from.
_ADDRESS_SUFFIX = re.compile(r"^/subinterface\[index=\d+\]/ipv4/address\[ip-prefix=[^\[\]]+\]$")

# A key may hold '/' (``ethernet-1/7``, ``192.0.2.1/31``), but never a bracket:
# one would mean the key swallowed part of a deeper path, which is a shape this
# planner does not know.
_PLAIN_KEY = re.compile(r"^[^\[\]]+$")

# A routed_interface key is a subinterface, ``X.N``.
_BINDING_KEY = re.compile(r"^(.+)\.\d+$")

_REMOVABLE_KINDS = ("interface", "bgp_session", "routed_interface")


def plan_removal(report: DriftReport) -> RemovalPlan:
    """The deletes that would remove ``report``'s undesired config, and what is refused.

    Pure: no I/O. Deterministic: deletes are sorted by path, refusals by
    entity path, and each delete's answers keep the report's order.

    Raises:
        ValueError: the report's status is ERROR, so there is nothing to plan from.
    """
    if report.status == DriftStatus.ERROR:
        raise ValueError(f"cannot plan a removal for {report.device_name}: its scan ended in error ({report.error})")

    protected = tuple(ENTITY_FIELD_MAP["interface"]["protected"])
    deletes: dict[str, tuple[str, list[str]]] = {}
    refused: dict[str, tuple[str, RemovalRefusal, list[str]]] = {}

    for item in report.items:
        if not item.undesired:
            continue
        target = _delete_target(item)
        if target is None:
            refused.setdefault(item.path, (item.entity_kind, RemovalRefusal.NOT_REMOVABLE, []))[2].append(item.path)
            continue
        path, interface = target
        if item.entity_kind not in _REMOVABLE_KINDS:
            refused.setdefault(path, (item.entity_kind, RemovalRefusal.NOT_REMOVABLE, []))[2].append(item.path)
        elif interface is not None and interface in protected:
            refused.setdefault(path, (item.entity_kind, RemovalRefusal.PROTECTED, []))[2].append(item.path)
        else:
            deletes.setdefault(path, (item.entity_kind, []))[1].append(item.path)

    plan = RemovalPlan(
        device_id=report.device_id,
        device_name=report.device_name,
        scanned_at=report.timestamp,
        deletes=[
            PlannedDelete(path=path, entity_kind=kind, answers=answers)
            for path, (kind, answers) in sorted(deletes.items())
        ],
        refused=[
            RefusedEntity(entity_path=path, entity_kind=kind, paths=paths, reason=reason)
            for path, (kind, reason, paths) in sorted(refused.items())
        ],
        forward_drift=[item.path for item in report.items if not item.undesired],
    )
    _check_every_path_placed(report, plan)
    return plan


def _delete_target(item: DriftItem) -> tuple[str, str | None] | None:
    """The path a delete of ``item``'s entity would name, and the interface it belongs to.

    ``None`` when the item's path fits no known shape for its kind. The
    interface is ``None`` for kinds that don't belong to one (a neighbour, a
    policy).
    """
    spec: dict[str, Any] | None = ENTITY_FIELD_MAP.get(item.entity_kind)
    if spec is None:
        return None

    if item.entity_kind == "interface":
        address = _extra_address(item.path, spec)
        if address is not None:
            return address

    entity, _, field = item.path.rpartition("/")
    match = _key_pattern(spec["path_template"]).match(entity)
    if field not in spec["fields"] or match is None or not _PLAIN_KEY.match(key := match.group(1)):
        return None
    if item.entity_kind == "routed_interface":
        binding = _BINDING_KEY.match(key)
        return (entity, binding.group(1)) if binding else None
    return entity, key if item.entity_kind == "interface" else None


def _extra_address(path: str, spec: dict[str, Any]) -> tuple[str, str] | None:
    """``(path, interface)`` when ``path`` is an address under an interface's entry, else None."""
    # A key holds no bracket, so the interface's entry ends at the first ']'.
    head, bracket, suffix = path.partition("]")
    match = _key_pattern(spec["path_template"]).match(head + bracket)
    if match and _PLAIN_KEY.match(match.group(1)) and _ADDRESS_SUFFIX.match(suffix):
        return path, match.group(1)
    return None


def _check_every_path_placed(report: DriftReport, plan: RemovalPlan) -> None:
    placed = sorted([p for d in plan.deletes for p in d.answers] + [p for r in plan.refused for p in r.paths])
    wanted = sorted(item.path for item in report.items if item.undesired)
    if placed != wanted:
        raise ValueError(f"removal plan for {report.device_name} does not place every undesired path once")
