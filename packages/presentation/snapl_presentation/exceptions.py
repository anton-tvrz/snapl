"""Presentation-block exceptions.

Every anticipated failure reaches the operator as one actionable line, never a
traceback (spec 006 FR-014). ``CliError`` carries the message and the exit code
together so the command layer never has to decide either separately.
"""

from __future__ import annotations

from snapl_presentation.exit_codes import ExitCode


class CliError(Exception):
    """An anticipated failure, rendered as a single line with an exit code."""

    def __init__(self, message: str, *, code: ExitCode = ExitCode.ERROR, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.hint = hint

    def render(self) -> str:
        """The operator-facing form: what broke, then what would fix it."""
        return f"{self.message}\n  {self.hint}" if self.hint else self.message


def first_line(text: str, *, limit: int = 160) -> str:
    """The first meaningful line of a multi-line error, bounded.

    SDK errors embed whole GraphQL documents and stack context. Pasting that
    into an operator's terminal buries the one fact they need (FR-014), so the
    cause is reduced to its first line and clipped.
    """
    line = next((part.strip() for part in str(text).splitlines() if part.strip()), "")
    return line if len(line) <= limit else f"{line[:limit].rstrip()}…"


class ConnectionCliError(CliError):
    """A dependency could not be reached.

    Always names the address tried and the env var that sets it — "connection
    refused" without those is a dead end for whoever is holding the terminal.
    """

    def __init__(self, *, subsystem: str, address: str, env_var: str, cause: str) -> None:
        super().__init__(
            f"{subsystem} unreachable at {address}: {first_line(cause)}",
            code=ExitCode.ERROR,
            hint=f"Set {env_var} if it lives elsewhere, or start the stack: uv run invoke dev.deps",
        )
        self.subsystem = subsystem
        self.address = address
        self.env_var = env_var


class UnknownDeviceError(CliError):
    """A device name did not resolve in the Source of Truth."""

    def __init__(self, name: str, *, known: list[str] | None = None) -> None:
        known = known or []
        hint = f"Known devices: {', '.join(sorted(known))}" if known else "Is the SoT seeded? uv run invoke demo.seed"
        super().__init__(f"no device named {name!r} in the Source of Truth", code=ExitCode.ERROR, hint=hint)
        self.name = name


class AmbiguousDeviceError(CliError):
    """A device name matched more than one device — refuse rather than guess."""

    def __init__(self, name: str, *, use_cases: list[str]) -> None:
        super().__init__(
            f"device name {name!r} is ambiguous — it exists in {len(use_cases)} use cases",
            code=ExitCode.ERROR,
            hint=f"Disambiguate with --use-case: {', '.join(sorted(use_cases))}",
        )
        self.name = name
        self.use_cases = use_cases
