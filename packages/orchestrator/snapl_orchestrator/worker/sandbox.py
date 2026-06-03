"""Workflow sandbox configuration shared by the worker and the workflow tests.

Temporal runs workflow code inside a sandbox that re-imports modules in an
isolated environment. The downstream blocks' result models (e.g.
``snapl_executor.models.ApplyResult``) use ``from __future__ import annotations``,
so their type hints are strings that pydantic must resolve when it builds a
``TypeAdapter`` to decode an activity result *inside the sandbox*. Under the
default sandbox that resolution fails with ``PydanticUserError: not fully
defined`` — the workflow task then crash-loops and the workflow appears to hang.

Marking the model-bearing modules as *passthrough* makes the sandbox reuse the
already-imported (fully defined) modules from the host interpreter instead of
re-importing them, which resolves the type hints correctly. These modules are
pure value objects with no non-deterministic behaviour, so passing them through
is safe for workflow determinism.
"""

from __future__ import annotations

from temporalio.worker.workflow_sandbox import (
    SandboxedWorkflowRunner,
    SandboxRestrictions,
)

# Only the result-model modules cross the workflow boundary — pass those through,
# not the blocks' I/O code, so workflow logic still runs sandboxed.
PASSTHROUGH_MODULES = (
    "snapl_intent.models",
    "snapl_executor.models",
    "snapl_collector.models",
    "snapl_observability.models",
)


def build_workflow_runner() -> SandboxedWorkflowRunner:
    """Return a sandboxed workflow runner that passes through the model modules."""
    return SandboxedWorkflowRunner(
        restrictions=SandboxRestrictions.default.with_passthrough_modules(*PASSTHROUGH_MODULES)
    )
