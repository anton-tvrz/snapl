"""ConfigRenderer — Jinja2 template loading and rendering (T015).

Templates live under packages/executor/snapl_executor/templates/<use_case>/.
Each template renders one entity type; renderer merges them into one payload.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateError

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

        Raises TemplateError if a template references a missing or undefined
        variable, or if an interface carries an IP address without a prefix
        length — interpolating a None prefix would produce an invalid
        ``ip-prefix`` the device rejects with an opaque error (#72).
        """
        for iface in desired.interfaces:
            if iface.ip_address and iface.prefix_length is None:
                raise TemplateError(f"interface {iface.name!r}: ip_address {iface.ip_address!r} has no prefix_length")

        ctx = {
            "device": desired.device,
            "interfaces": desired.interfaces,
            "sessions": desired.bgp_sessions,
        }

        ifaces_raw: list[dict] = json.loads(self._env.get_template("interfaces.j2").render(**ctx))

        # Intent-first: no synthetic entities — the seeded lo0 is the loopback,
        # a hardcoded one collided with it and carried the wrong address (#78).
        payload: dict[str, Any] = {"interface": ifaces_raw}
        if desired.bgp_sessions:
            bgp_raw: dict = json.loads(self._env.get_template("bgp.j2").render(**ctx))
            payload["network-instance"] = [
                {
                    "name": "default",
                    "protocols": {"bgp": bgp_raw},
                }
            ]
        return payload

    def render_safe(self, desired: DesiredState) -> dict[str, Any]:
        """Render without raising — returns {_render_error: msg} on failure."""
        try:
            return self.render(desired)
        except (TemplateError, json.JSONDecodeError) as exc:
            return {_RENDER_ERROR_KEY: str(exc)}


RENDER_ERROR_KEY = _RENDER_ERROR_KEY
