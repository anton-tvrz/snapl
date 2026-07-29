"""3-batch schema provisioning against Infrahub.

Schema YAML files live under ``packages/intent/snapl_intent/schemas/`` and are
loaded in three dependency-ordered batches:

    Batch 0 — base/        (dcim, ipam, location, organization)
    Batch 1 — extensions/  (routing_bgp, vrf, ...)
    Batch 2 — project-specific YAMLs directly under schemas/

Each batch must fully land in Infrahub before the next is loaded, because
extensions reference base types and project extensions reference both.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

import yaml

from snapl_intent.exceptions import IntentConnectionError, IntentSchemaError
from snapl_intent.models import ProvisionResult

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

BATCH_BASE = 0
BATCH_EXTENSIONS = 1
BATCH_PROJECT = 2

# Bounds for the post-load schema-readiness poll. Infrahub applies a loaded
# schema asynchronously (its task-worker registers the kinds/attributes after
# ``schema.load`` returns), so seeding immediately would race that registration
# and the SDK would silently drop unknown attributes from create payloads (#87).
_READINESS_TIMEOUT_S = 30.0
_READINESS_INTERVAL_S = 0.5

_BATCH_DIRS = {
    BATCH_BASE: "base",
    BATCH_EXTENSIONS: "extensions",
}


def discover_schema_batches(schemas_root: Path) -> list[list[Path]]:
    """Return the three dependency-ordered batches of schema files.

    Returns a list of 3 lists: ``[base_files, extension_files, project_files]``.
    Project-specific files live directly under ``schemas_root``; subdirectory
    files belong to base or extensions.
    """
    if not schemas_root.is_dir():
        raise IntentSchemaError(f"Schemas directory does not exist: {schemas_root}")

    base = sorted((schemas_root / _BATCH_DIRS[BATCH_BASE]).glob("*.yml"))
    extensions = sorted((schemas_root / _BATCH_DIRS[BATCH_EXTENSIONS]).glob("*.yml"))
    project = sorted(p for p in schemas_root.glob("*.yml") if p.is_file())

    return [base, extensions, project]


def collect_extension_attributes(schemas: Iterable[dict[str, Any]]) -> dict[str, set[str]]:
    """Map each extended kind to the attribute names our YAML adds to it.

    Project schema files extend existing kinds via ``extensions.nodes[].kind``
    with new ``attributes`` (e.g. ``DcimDevice`` gains ``use_case`` and
    ``lab_node_name``). These are exactly the attributes Infrahub registers
    asynchronously and the SDK silently drops from create payloads if seeding
    races ahead (#87) — so they are what the readiness poll waits on. Extension
    target kinds always surface in ``schema.all`` (they extend live kinds), so
    requiring them carries no false-timeout risk from abstract generics.
    """
    expected: dict[str, set[str]] = {}
    for schema in schemas:
        nodes = ((schema or {}).get("extensions") or {}).get("nodes") or []
        for node in nodes:
            kind = node.get("kind")
            if not kind:
                continue
            names = {a["name"] for a in (node.get("attributes") or []) if a.get("name")}
            if names:
                expected.setdefault(kind, set()).update(names)
    return expected


def _live_attribute_names(schema_node: Any) -> set[str]:
    """Attribute names a live schema node (``NodeSchemaAPI``) exposes."""
    attributes = getattr(schema_node, "attributes", None) or []
    return {name for name in (getattr(a, "name", None) for a in attributes) if name}


def _unsatisfied_schema(
    registry: Any,
    expected: dict[str, set[str]],
) -> dict[str, set[str]]:
    """Return the expected kind→attributes not yet visible in the live registry."""
    registry = registry or {}
    missing: dict[str, set[str]] = {}
    for kind, attrs in expected.items():
        live = _live_attribute_names(registry.get(kind)) if hasattr(registry, "get") else set()
        absent = attrs - live
        if absent:
            missing[kind] = absent
    return missing


def _extract_errors(response: Any) -> str | None:
    """Return a concatenated error string from an SDK response, or ``None``."""
    errors = response.get("errors") if isinstance(response, dict) else getattr(response, "errors", None)

    if not errors:
        return None

    if isinstance(errors, list):
        return "; ".join((e.get("message") or str(e)) if isinstance(e, dict) else str(e) for e in errors)
    if isinstance(errors, dict):
        parts = []
        for key, value in errors.items():
            if isinstance(value, list):
                parts.append(f"{key}: " + "; ".join(str(v) for v in value))
            else:
                parts.append(f"{key}: {value}")
        return "; ".join(parts)
    return str(errors)


class SchemaLoader:
    """Load schema YAML files into Infrahub in dependency order."""

    def __init__(
        self,
        *,
        client: Any,
        schemas_root: Path,
        use_case: str = "dcfabric",
        branch: str | None = None,
        readiness_timeout: float = _READINESS_TIMEOUT_S,
        readiness_interval: float = _READINESS_INTERVAL_S,
    ) -> None:
        self._client = client
        self._schemas_root = schemas_root
        self._use_case = use_case
        self._branch = branch
        self._readiness_timeout = readiness_timeout
        self._readiness_interval = readiness_interval

    async def load(self) -> ProvisionResult:
        batches = discover_schema_batches(self._schemas_root)
        total_loaded = 0
        parsed: list[dict[str, Any]] = []

        for batch in batches:
            if not batch:
                continue
            schemas = [self._parse_yaml(path) for path in batch]
            parsed.extend(schemas)
            try:
                response = await self._client.schema.load(
                    schemas=schemas,
                    branch=self._branch,
                )
            except (OSError, TimeoutError) as exc:
                raise IntentConnectionError(f"Infrahub unreachable: {exc}") from exc

            error_text = _extract_errors(response)
            if error_text:
                raise IntentSchemaError(f"Schema validation failed: {error_text}")

            total_loaded += len(batch)

        # Infrahub applies the schema asynchronously; block until the extension
        # attributes we just loaded are visible so a following seed doesn't race
        # registration and silently drop them (#87).
        await self._await_schema_ready(collect_extension_attributes(parsed))

        return ProvisionResult(
            use_case=self._use_case,
            schemas_loaded=total_loaded,
            changed=True,
        )

    async def _await_schema_ready(self, expected: dict[str, set[str]]) -> None:
        """Poll the live schema until every expected kind exposes its attributes.

        Uses ``refresh=True`` so each poll re-fetches from the server (bypassing
        the SDK's cached schema, which is the thing lagging) and leaves the SDK
        cache current for the subsequent seed. Bounded — raises on timeout.
        """
        if not expected:
            return

        deadline = time.monotonic() + self._readiness_timeout
        missing: dict[str, set[str]] = {}
        while True:
            try:
                registry = await self._client.schema.all(branch=self._branch, refresh=True)
            except (OSError, TimeoutError) as exc:
                raise IntentConnectionError(f"Infrahub unreachable: {exc}") from exc

            missing = _unsatisfied_schema(registry, expected)
            if not missing:
                return
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(self._readiness_interval)

        detail = "; ".join(f"{kind}: {', '.join(sorted(attrs))}" for kind, attrs in sorted(missing.items()))
        raise IntentSchemaError(
            f"Schema not ready after {self._readiness_timeout}s — attributes not registered: {detail}"
        )

    def _parse_yaml(self, path: Path) -> dict[str, Any]:
        try:
            data = yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            raise IntentSchemaError(f"Malformed schema YAML {path}: {exc}") from exc
        return data or {}
