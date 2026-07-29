"""The CLI's exit-code contract (spec 006 FR-011).

Three outcomes, uniform across every command, so a caller that reads only the
exit code can tell them apart without parsing output:

    0  OK       the command ran and found nothing wrong
    1  ERROR    an operational failure — unreachable dependency, bad input,
                a workflow that did not succeed
    2  DRIFT    the command ran successfully and found drift

Drift is deliberately not an error. A scan that finds drift did its job
perfectly; conflating that with "the scan broke" leaves a cron job unable to
distinguish a fabric that needs attention from a monitoring pipeline that does.
This is the check-tool convention (diff, grep -q, shellcheck).
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    ERROR = 1
    DRIFT = 2
