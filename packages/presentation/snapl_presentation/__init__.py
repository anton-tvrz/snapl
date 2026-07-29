"""NAF Presentation building block — CLI / API interface.

The ``snapl`` console entry point (spec 006). A thin client over the
Orchestrator's workflows: it starts them, renders their results, and maps
outcomes onto a uniform exit-code contract. No network logic lives here.
"""

from snapl_presentation.exceptions import CliError
from snapl_presentation.exit_codes import ExitCode
from snapl_presentation.settings import CliSettings

__all__ = ["CliError", "CliSettings", "ExitCode"]
