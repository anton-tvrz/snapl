"""ConfigRenderer — Jinja2 template loading and rendering (T015).

Templates live under packages/executor/snapl_executor/templates/<use_case>/.
Each template renders one entity type; renderer merges them into one payload.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, UndefinedError

if TYPE_CHECKING:
    from snapl_intent.models import DesiredState

_TEMPLATES_ROOT = Path(__file__).resolve().parent.parent / "templates"

_RENDER_ERROR_KEY = "_render_error"


class ConfigRenderer:
    """Renders a DesiredState into a SR Linux YANG-modelled JSON payload."""

    def __init__(self, *, use_case: str) -> None:
        self.use_case = use_case
        template_dir = _TEMPLATES_ROOT / use_case
        if not template_dir.is_dir():
            raise FileNotFoundError(f"No templates found for use case {use_case!r}: {template_dir}")
        self._env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            autoescape=False,
            undefined=StrictUndefined,
        )

    def render(self, desired: DesiredState) -> dict[str, Any]:
        """Render all templates and merge into one payload dict.

        Raises UndefinedError if a template references a missing variable.
        """
        ctx = {
            "device": desired.device,
            "interfaces": desired.interfaces,
            "sessions": desired.bgp_sessions,
        }

        ifaces_raw: list[dict] = json.loads(self._env.get_template("interfaces.j2").render(**ctx))
        loopback: dict = json.loads(self._env.get_template("system.j2").render(**ctx))
        bgp_raw: dict = json.loads(self._env.get_template("bgp.j2").render(**ctx))

        all_interfaces = [*ifaces_raw, loopback]

        payload: dict[str, Any] = {
            "interface": all_interfaces,
            "network-instance": [
                {
                    "name": "default",
                    "protocols": {
                        "bgp": bgp_raw,
                    },
                }
            ],
        }
        return payload

    def render_safe(self, desired: DesiredState) -> dict[str, Any]:
        """Render without raising — returns {_render_error: msg} on failure."""
        try:
            return self.render(desired)
        except (UndefinedError, Exception) as exc:
            return {_RENDER_ERROR_KEY: str(exc)}


RENDER_ERROR_KEY = _RENDER_ERROR_KEY
