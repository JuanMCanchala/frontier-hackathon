"""Execution interface: the supervisor depends on this, not on a concrete agent."""

from typing import Protocol

from aprendiz.models import RunContext, RunResult


class Runner(Protocol):
    async def run(self, ctx: RunContext) -> RunResult:
        """Execute a task. Should release its desktop connection on cancellation;
        the supervisor revokes the lease and force-closes registered clients anyway."""
        ...
