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

from typing import TYPE_CHECKING, Any

import yaml

from snapl_intent.exceptions import IntentConnectionError, IntentSchemaError
from snapl_intent.models import ProvisionResult

if TYPE_CHECKING:
    from pathlib import Path

BATCH_BASE = 0
BATCH_EXTENSIONS = 1
BATCH_PROJECT = 2

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
    ) -> None:
        self._client = client
        self._schemas_root = schemas_root
        self._use_case = use_case
        self._branch = branch

    async def load(self) -> ProvisionResult:
        batches = discover_schema_batches(self._schemas_root)
        total_loaded = 0

        for batch in batches:
            if not batch:
                continue
            schemas = [self._parse_yaml(path) for path in batch]
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

        return ProvisionResult(
            use_case=self._use_case,
            schemas_loaded=total_loaded,
            changed=True,
        )

    def _parse_yaml(self, path: Path) -> dict[str, Any]:
        try:
            data = yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            raise IntentSchemaError(f"Malformed schema YAML {path}: {exc}") from exc
        return data or {}
