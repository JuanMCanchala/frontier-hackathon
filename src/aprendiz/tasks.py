"""Supervisor: runs tasks in the background, one per desktop, with a hard deadline.

Shutdown order for a task that expires or is displaced:
revoke lease -> cancel runner -> wait grace -> release held input and close VNC
-> free the desktop -> publish.
The supervisor never relies on the runner honouring cancellation.
"""

import asyncio
import logging
import time
import uuid

from aprendiz.config import Settings
from aprendiz.models import Escritorio, RunContext, RunResult, TaskStatus
from aprendiz.redact import redact
from aprendiz.runner import Runner
from aprendiz.store import TaskStore
from aprendiz.vnc import Lease, VncClient

log = logging.getLogger(__name__)

SUMMARY_TIMEOUT = "Se agotó el tiempo límite de la tarea."
SUMMARY_CANCELLED = "Tarea cancelada: llegó una tarea nueva para el mismo escritorio."
SUMMARY_ERROR = "La tarea falló por un error interno."
SUMMARY_RESTART = "El servicio se reinició durante la tarea."
SUMMARY_SHUTDOWN = "El servicio se detuvo durante la tarea."

_TIMEOUT = RunResult(estado="fallida", resumen=SUMMARY_TIMEOUT)
_CANCELLED = RunResult(estado="fallida", resumen=SUMMARY_CANCELLED)
_ERROR = RunResult(estado="fallida", resumen=SUMMARY_ERROR)


class _Entry:
    def __init__(
        self,
        task_id: str,
        tarea: str,
        escritorio: Escritorio,
        actor_deadline: float,
        deadline: float,
    ) -> None:
        self.lease = Lease()
        self.clients: list[VncClient] = []
        self.displaced = asyncio.Event()
        self.finished = asyncio.Event()
        self.supervisor: asyncio.Task[None] | None = None
        self.ctx = RunContext(
            task_id=task_id,
            tarea=tarea,
            escritorio=escritorio,
            actor_deadline=actor_deadline,
            deadline=deadline,
            lease=self.lease,
            register_client=self.clients.append,
        )

    async def close_clients(self) -> None:
        for client in self.clients:
            await client.force_close()


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


class TaskManager:
    def __init__(self, settings: Settings, runner: Runner) -> None:
        self._settings = settings
        self._runner = runner
        self._store = TaskStore(settings.TASKS_DB)
        restarted = self._store.fail_unfinished(SUMMARY_RESTART)
        if restarted:
            log.warning("marked unfinished tasks as failed", extra={"count": restarted})
        self._active: dict[str, _Entry] = {}

    async def submit(self, tarea: str, escritorio: Escritorio) -> str:
        # The deadline starts when the POST is accepted, not when the runner starts.
        accepted = time.monotonic()
        actor_deadline = accepted + self._settings.ACTOR_STOP_S
        deadline = accepted + self._settings.TASK_DEADLINE_S
        task_id = uuid.uuid4().hex
        await self._store.create(task_id)
        entry = _Entry(task_id, tarea, escritorio, actor_deadline, deadline)
        # No awaits between reading and replacing the active entry, so concurrent
        # submissions for one desktop form a strict chain.
        previous = self._active.get(escritorio.identity)
        entry.supervisor = asyncio.create_task(self._supervise(entry, previous))
        self._active[escritorio.identity] = entry
        return task_id

    async def status(self, task_id: str) -> TaskStatus | None:
        return await self._store.get(task_id)

    async def shutdown(self) -> None:
        supervisors = [e.supervisor for e in self._active.values() if e.supervisor]
        for supervisor in supervisors:
            supervisor.cancel()
        await asyncio.gather(*supervisors, return_exceptions=True)
        self._store.close()

    async def _supervise(self, entry: _Entry, previous: _Entry | None) -> None:
        cancelled = False
        try:
            result = await self._execute(entry, previous)
        except asyncio.CancelledError:
            cancelled = True
            result = RunResult(estado="fallida", resumen=SUMMARY_SHUTDOWN)
            entry.lease.revoke()
            await entry.close_clients()
        finally:
            # The desktop is freed before publishing: nothing touches it past this point.
            self._release(entry)
        await self._publish(entry, result)
        if cancelled:
            raise asyncio.CancelledError

    def _release(self, entry: _Entry) -> None:
        entry.finished.set()
        key = entry.ctx.escritorio.identity
        if self._active.get(key) is entry:
            del self._active[key]

    async def _execute(self, entry: _Entry, previous: _Entry | None) -> RunResult:
        if previous is not None:
            early = await self._wait_for_previous(entry, previous)
            if early is not None:
                return early
        runner_task = asyncio.create_task(self._runner.run(entry.ctx))
        runner_task.add_done_callback(lambda t: t.cancelled() or t.exception())
        displaced = asyncio.ensure_future(entry.displaced.wait())
        try:
            done, _ = await asyncio.wait(
                {runner_task, displaced},
                timeout=_remaining(entry.ctx.deadline),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if runner_task in done:
                return self._runner_outcome(entry, runner_task)
            return _CANCELLED if displaced in done else _TIMEOUT
        finally:
            displaced.cancel()
            await self._stop(entry, runner_task)

    def _runner_outcome(self, entry: _Entry, runner_task: asyncio.Task[RunResult]) -> RunResult:
        error = runner_task.exception()
        if error is None:
            return runner_task.result()
        log.error("runner failed", extra={"task_id": entry.ctx.task_id}, exc_info=error)
        return _ERROR

    async def _wait_for_previous(self, entry: _Entry, previous: _Entry) -> RunResult | None:
        """Displace the previous task and wait for its orderly close.

        The wait is capped by PREVIOUS_RELEASE_S and by this task's own deadline. Its
        lease is revoked first, so past the cap it can no longer send input and this
        task may proceed even if the old runner is still alive.
        """
        previous.lease.revoke()
        previous.displaced.set()
        previous_done = asyncio.ensure_future(previous.finished.wait())
        displaced = asyncio.ensure_future(entry.displaced.wait())
        try:
            done, _ = await asyncio.wait(
                {previous_done, displaced},
                timeout=min(self._settings.PREVIOUS_RELEASE_S, _remaining(entry.ctx.deadline)),
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            previous_done.cancel()
            displaced.cancel()
        if previous_done in done:
            return None
        if displaced in done:
            return _CANCELLED
        if _remaining(entry.ctx.deadline) > 0:
            log.warning("previous task did not close in time", extra={"task_id": entry.ctx.task_id})
            return None
        return _TIMEOUT

    async def _stop(self, entry: _Entry, runner_task: asyncio.Task[RunResult]) -> None:
        entry.lease.revoke()
        if not runner_task.done():
            runner_task.cancel()
            await asyncio.wait({runner_task}, timeout=self._settings.STOP_GRACE_S)
            if not runner_task.done():
                log.warning("runner ignored cancellation", extra={"task_id": entry.ctx.task_id})
        await entry.close_clients()

    async def _publish(self, entry: _Entry, result: RunResult) -> None:
        secrets = (
            entry.ctx.escritorio.password,
            self._settings.TEAM_TOKEN,
            self._settings.OPENROUTER_API_KEY,
        )
        status = TaskStatus(estado=result.estado, resumen=redact(result.resumen, secrets))
        await self._store.set(entry.ctx.task_id, status)
        log.info("task finished", extra={"task_id": entry.ctx.task_id, "estado": status.estado})
