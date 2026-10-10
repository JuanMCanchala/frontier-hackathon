import asyncio
import struct
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path

import httpx
import pytest

from aprendiz.api import create_app
from aprendiz.config import Settings, VncTimings
from aprendiz.models import Escritorio, RunContext, RunResult, TaskStatus
from aprendiz.tasks import TaskManager
from aprendiz.vnc import VncClient
from tests.fake_server import PASSWORD, FakeRfbServer

TOKEN = "team-token-test"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
BODY = {"tarea": "abre la calculadora", "escritorio": {"url": "ws://host:1/x", "password": "pw"}}


def make_settings(db: str, deadline: float = 5) -> Settings:
    return Settings(
        OPENROUTER_API_KEY="sk-or-test-key-0000",
        TEAM_TOKEN=TOKEN,
        TASK_DEADLINE_S=deadline,
        TASKS_DB=db,
    )


class FakeRunner:
    """Configurable runner that records cleanup so tests can order events."""

    def __init__(
        self,
        delay: float = 0,
        result: RunResult | None = None,
        error: bool = False,
        cleanup_s: float = 0,
        cleanup_only: str | None = None,
    ) -> None:
        self.delay = delay
        self.result = result or RunResult(estado="terminada", resumen="listo")
        self.error = error
        self.cleanup_s = cleanup_s
        self.cleanup_only = cleanup_only
        self.started: list[str] = []
        self.cleaned: list[str] = []
        self.completed: list[str] = []

    async def run(self, ctx: RunContext) -> RunResult:
        tarea = ctx.tarea
        self.started.append(tarea)
        try:
            if self.error:
                raise RuntimeError("boom at /internal/path with secret-detail")
            await asyncio.sleep(self.delay)
            self.completed.append(tarea)
            return self.result
        finally:
            if self.cleanup_only in (None, tarea):
                await asyncio.sleep(self.cleanup_s)
            self.cleaned.append(tarea)


Factory = Callable[[FakeRunner, float], AbstractAsyncContextManager[httpx.AsyncClient]]


@pytest.fixture
def make_client(tmp_path: Path) -> Factory:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def factory(runner: FakeRunner, deadline: float = 5) -> AsyncIterator[httpx.AsyncClient]:
        app = create_app(make_settings(str(tmp_path / "t.db"), deadline), runner)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                yield client

    return factory


async def wait_final(client: httpx.AsyncClient, task_id: str, timeout: float = 5) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        data = (await client.get(f"/tasks/{task_id}", headers=AUTH)).json()
        if data["estado"] != "en_curso":
            return data
        await asyncio.sleep(0.01)
    raise AssertionError("task did not finish")


async def test_health_needs_no_token(make_client: Factory) -> None:
    async with make_client(FakeRunner(), 5) as c:
        r = await c.get("/health")
        assert r.status_code == 200 and r.json() == {"ok": True}


async def test_tasks_require_valid_token(make_client: Factory) -> None:
    async with make_client(FakeRunner(), 5) as c:
        assert (await c.post("/tasks", json=BODY)).status_code == 401
        bad = {"Authorization": "Bearer nope"}
        assert (await c.post("/tasks", json=BODY, headers=bad)).status_code == 401
        assert (await c.get("/tasks/abc")).status_code == 401
        assert (await c.get("/tasks/abc", headers=bad)).status_code == 401
        assert (await c.get("/tasks/abc", headers=AUTH)).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"tarea": "", "escritorio": {"url": "ws://h:1", "password": "p"}},
        {"tarea": "   ", "escritorio": {"url": "ws://h:1", "password": "p"}},
        {"tarea": "x", "escritorio": {"url": "http://h:1", "password": "p"}},
        {"tarea": "x", "escritorio": {"url": "ws://h:1"}},
        {"tarea": "x"},
    ],
)
async def test_invalid_body_is_422(make_client: Factory, body: dict) -> None:
    async with make_client(FakeRunner(), 5) as c:
        assert (await c.post("/tasks", json=body, headers=AUTH)).status_code == 422


async def test_post_returns_fast_even_if_runner_is_slow(make_client: Factory) -> None:
    async with make_client(FakeRunner(delay=10), 30) as c:
        start = time.monotonic()
        r = await c.post("/tasks", json=BODY, headers=AUTH)
        assert time.monotonic() - start < 2
        assert r.status_code == 200 and list(r.json()) == ["task_id"]
        status = (await c.get(f"/tasks/{r.json()['task_id']}", headers=AUTH)).json()
        assert status["estado"] == "en_curso"


async def test_transition_to_terminada(make_client: Factory) -> None:
    runner = FakeRunner(delay=0.05)
    async with make_client(runner, 5) as c:
        task_id = (await c.post("/tasks", json=BODY, headers=AUTH)).json()["task_id"]
        first = (await c.get(f"/tasks/{task_id}", headers=AUTH)).json()
        assert first["estado"] == "en_curso"
        final = await wait_final(c, task_id)
        assert final == {"estado": "terminada", "resumen": "listo"}


async def test_exception_gives_failed_with_generic_summary(make_client: Factory) -> None:
    async with make_client(FakeRunner(error=True), 5) as c:
        task_id = (await c.post("/tasks", json=BODY, headers=AUTH)).json()["task_id"]
        final = await wait_final(c, task_id)
        assert final["estado"] == "fallida"
        assert "boom" not in final["resumen"] and "internal" not in final["resumen"]


async def test_deadline_publishes_only_after_runner_cleanup(make_client: Factory) -> None:
    runner = FakeRunner(delay=10, cleanup_s=0.3)
    async with make_client(runner, 0.2) as c:
        task_id = (await c.post("/tasks", json=BODY, headers=AUTH)).json()["task_id"]
        seen_cleaned_at_publish = None
        end = time.monotonic() + 5
        while time.monotonic() < end:
            data = (await c.get(f"/tasks/{task_id}", headers=AUTH)).json()
            if data["estado"] != "en_curso":
                seen_cleaned_at_publish = bool(runner.cleaned)
                break
            await asyncio.sleep(0.005)
        assert data["estado"] == "fallida"
        assert "tiempo" in data["resumen"]
        assert seen_cleaned_at_publish is True
        assert runner.completed == []


async def test_summary_is_redacted(make_client: Factory) -> None:
    leaky = RunResult(estado="terminada", resumen="pw y team-token-test y 901234567")
    async with make_client(FakeRunner(result=leaky), 5) as c:
        task_id = (await c.post("/tasks", json=BODY, headers=AUTH)).json()["task_id"]
        final = await wait_final(c, task_id)
        assert "team-token-test" not in final["resumen"]
        assert final["resumen"].endswith("901234567")


async def test_new_task_on_same_desktop_cancels_previous(make_client: Factory) -> None:
    runner = FakeRunner(delay=10, cleanup_s=0.2)
    async with make_client(runner, 30) as c:
        first = (await c.post("/tasks", json={**BODY, "tarea": "uno"}, headers=AUTH)).json()
        await asyncio.sleep(0.05)
        runner.delay = 0
        second = (await c.post("/tasks", json={**BODY, "tarea": "dos"}, headers=AUTH)).json()

        final_two = await wait_final(c, second["task_id"])
        final_one = await wait_final(c, first["task_id"])
        assert final_one["estado"] == "fallida"
        assert final_two["estado"] == "terminada"
        # "dos" only started once "uno" had finished cleaning up.
        assert runner.cleaned[0] == "uno"
        assert runner.started == ["uno", "dos"]
        assert runner.completed == ["dos"]


async def test_wait_for_previous_is_capped(tmp_path: Path) -> None:
    settings = make_settings(str(tmp_path / "t.db"), 30).model_copy(
        update={"PREVIOUS_RELEASE_S": 0.3, "STOP_GRACE_S": 5.0}
    )
    runner = FakeRunner(delay=10, cleanup_s=3, cleanup_only="uno")
    manager = TaskManager(settings, runner)
    escritorio = Escritorio(url="ws://h:1/x", password="pw")
    try:
        await manager.submit("uno", escritorio)
        await asyncio.sleep(0.05)
        runner.delay = 0
        start = time.monotonic()
        second = await manager.submit("dos", escritorio)
        while (await manager.status(second)).estado == "en_curso":  # type: ignore[union-attr]
            await asyncio.sleep(0.01)
        # "uno" is still cleaning up (3 s) but its lease is revoked: "dos" goes on at the cap.
        assert time.monotonic() - start < 1.0
        assert runner.completed == ["dos"]
        assert "uno" not in runner.cleaned
    finally:
        await manager.shutdown()


async def test_tasks_on_other_desktops_run_in_parallel(make_client: Factory) -> None:
    runner = FakeRunner(delay=0.2)
    other = {**BODY, "escritorio": {"url": "ws://otro:2/x", "password": "pw"}}
    async with make_client(runner, 5) as c:
        a = (await c.post("/tasks", json=BODY, headers=AUTH)).json()["task_id"]
        b = (await c.post("/tasks", json=other, headers=AUTH)).json()["task_id"]
        assert (await wait_final(c, a))["estado"] == "terminada"
        assert (await wait_final(c, b))["estado"] == "terminada"


async def test_deadline_counts_from_the_post(make_client: Factory) -> None:
    runner = FakeRunner(delay=10, cleanup_s=0.5, cleanup_only="uno")
    async with make_client(runner, 1.0) as c:
        await c.post("/tasks", json={**BODY, "tarea": "uno"}, headers=AUTH)
        await asyncio.sleep(0.1)
        start = time.monotonic()
        second = (await c.post("/tasks", json={**BODY, "tarea": "dos"}, headers=AUTH)).json()
        final = await wait_final(c, second["task_id"])
        elapsed = time.monotonic() - start
        assert "dos" in runner.started  # it did start, after waiting for "uno" to release
        assert final["estado"] == "fallida" and "tiempo" in final["resumen"]
        # Counted from its own POST (~1.0 s); counting from runner start would be ~1.5 s.
        assert 0.9 <= elapsed < 1.3


class RogueRunner:
    """Swallows cancellation and keeps trying to click forever."""

    def __init__(self) -> None:
        self.attempts = 0
        self.rejected = 0
        # Only the test may stop it: an orphan that swallows cancellation would
        # otherwise hang the event loop teardown.
        self.stop = False

    async def run(self, ctx: RunContext) -> RunResult:
        client = VncClient(VncTimings(click_hold_s=0.01), lease=ctx.lease)
        await client.connect(ctx.escritorio.url, ctx.escritorio.password)
        ctx.register_client(client)
        while not self.stop:
            self.attempts += 1
            try:
                await client.click(5, 5)
            except BaseException:
                self.rejected += 1
            # A revoked click raises before any await; yield so the loop is not starved.
            try:
                await asyncio.sleep(0.01)
            except BaseException:
                pass
        return RunResult(estado="terminada", resumen="")


async def test_runner_ignoring_cancellation_cannot_touch_the_desktop(make_client: Factory) -> None:
    server = FakeRfbServer()
    await server.start()
    rogue = RogueRunner()
    deadline = 0.6
    try:
        async with make_client(rogue, deadline) as c:  # type: ignore[arg-type]
            body = {**BODY, "escritorio": {"url": server.url, "password": PASSWORD}}
            start = time.monotonic()
            task_id = (await c.post("/tasks", json=body, headers=AUTH)).json()["task_id"]
            final = await wait_final(c, task_id)
            published = time.monotonic() - start
            events_at_publish = len(server.pointer_events)
            await asyncio.sleep(0.4)
        assert final["estado"] == "fallida" and "tiempo" in final["resumen"]
        assert published < deadline + 2.5
        assert rogue.attempts > 3 and rogue.rejected > 0
        assert len(server.pointer_events) == events_at_publish
        # Only a button-up may follow the cut (release of a click caught mid-way).
        late = [e for t, e in server.pointer_events if t > start + deadline + 0.05]
        assert all(e[0] == 0 for e in late)
        assert server.pointer_events[-1][1][0] == 0
    finally:
        rogue.stop = True
        await server.stop()


async def test_restart_marks_running_tasks_failed(tmp_path: Path) -> None:
    settings = make_settings(str(tmp_path / "t.db"))
    escritorio = Escritorio(url="ws://h:1/x", password="pw")
    first = TaskManager(settings, FakeRunner(delay=10))
    task_id = await first.submit("algo", escritorio)
    assert (await first.status(task_id)).estado == "en_curso"  # type: ignore[union-attr]

    second = TaskManager(settings, FakeRunner())  # same DB: simulates a process restart
    status = await second.status(task_id)
    assert status == TaskStatus(
        estado="fallida", resumen="El servicio se reinició durante la tarea."
    )
    await second.shutdown()
    await first.shutdown()


async def test_db_never_stores_password_or_url(make_client: Factory, tmp_path: Path) -> None:
    body = {
        "tarea": "t",
        "escritorio": {"url": "ws://host-secreto:5999/path", "password": "pw-ultra-secreto"},
    }
    leaky = RunResult(estado="terminada", resumen="usé pw-ultra-secreto")
    async with make_client(FakeRunner(result=leaky), 5) as c:
        task_id = (await c.post("/tasks", json=body, headers=AUTH)).json()["task_id"]
        await wait_final(c, task_id)
    raw = (tmp_path / "t.db").read_bytes()
    assert b"pw-ultra-secreto" not in raw
    assert b"host-secreto" not in raw


class HangingRunner:
    """Leaves the left button and Ctrl pressed, then hangs ignoring cancellation."""

    def __init__(self) -> None:
        self.stop = False  # see RogueRunner: lets the test end the orphan

    async def run(self, ctx: RunContext) -> RunResult:
        client = VncClient(VncTimings(), lease=ctx.lease)
        await client.connect(ctx.escritorio.url, ctx.escritorio.password)
        ctx.register_client(client)
        await client._pointer(7, 9, 1)
        await client._key_event(0xFFE3, True)
        while not self.stop:
            try:
                await asyncio.sleep(10)
            except BaseException:
                continue
        return RunResult(estado="terminada", resumen="")


async def test_cut_releases_held_input_before_closing(make_client: Factory) -> None:
    server = FakeRfbServer()
    await server.start()
    try:
        hanging = HangingRunner()
        async with make_client(hanging, 0.4) as c:  # type: ignore[arg-type]
            body = {**BODY, "escritorio": {"url": server.url, "password": PASSWORD}}
            task_id = (await c.post("/tasks", json=body, headers=AUTH)).json()["task_id"]
            final = await wait_final(c, task_id)
            await asyncio.sleep(0.3)
        assert final["estado"] == "fallida"
        pointer = [e for _, e in server.pointer_events]
        assert pointer == [
            bytes([1]) + struct.pack(">HH", 7, 9),
            bytes([0]) + struct.pack(">HH", 7, 9),
        ]
        assert server.key_events == [
            struct.pack(">BxxI", 1, 0xFFE3),
            struct.pack(">BxxI", 0, 0xFFE3),
        ]
        assert server.closed_at is not None
        assert all(t <= server.closed_at for t, _ in server.pointer_events)
        assert max(server.key_times) <= server.closed_at
    finally:
        hanging.stop = True
        await server.stop()


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        ("ws://Host/a", "ws://host:80/b?q=1", True),
        ("wss://host", "ws://host:443/x", True),
        ("ws://host", "wss://host", False),
        ("ws://host:5900", "ws://host:5901", False),
        ("ws://host-a:1", "ws://host-b:1", False),
    ],
)
def test_desktop_identity(a: str, b: str, same: bool) -> None:
    ea, eb = Escritorio(url=a, password="p"), Escritorio(url=b, password="p")
    assert (ea.identity == eb.identity) is same


async def test_differently_written_urls_share_one_desktop(make_client: Factory) -> None:
    runner = FakeRunner(delay=10, cleanup_s=0.1)
    async with make_client(runner, 30) as c:
        one = {**BODY, "tarea": "uno", "escritorio": {"url": "ws://HOST:80/a", "password": "p"}}
        two = {**BODY, "tarea": "dos", "escritorio": {"url": "ws://host/b?x=1", "password": "p"}}
        first = (await c.post("/tasks", json=one, headers=AUTH)).json()["task_id"]
        await asyncio.sleep(0.05)
        runner.delay = 0
        second = (await c.post("/tasks", json=two, headers=AUTH)).json()["task_id"]
        assert (await wait_final(c, second))["estado"] == "terminada"
        assert (await wait_final(c, first))["estado"] == "fallida"
        assert runner.cleaned[0] == "uno"


async def test_desktop_is_free_before_status_is_published(tmp_path: Path) -> None:
    settings = make_settings(str(tmp_path / "t.db"), deadline=0.2)
    manager = TaskManager(settings, FakeRunner(delay=10))
    task_id = await manager.submit("x", Escritorio(url="ws://h:1/x", password="p"))
    seen_active_at_publish = None
    while (await manager.status(task_id)).estado == "en_curso":  # type: ignore[union-attr]
        await asyncio.sleep(0.005)
    seen_active_at_publish = bool(manager._active)
    assert seen_active_at_publish is False
    await manager.shutdown()


async def test_context_carries_both_deadlines(tmp_path: Path) -> None:
    seen: list[RunContext] = []

    class Spy:
        async def run(self, ctx: RunContext) -> RunResult:
            seen.append(ctx)
            return RunResult(estado="terminada", resumen="ok")

    settings = make_settings(str(tmp_path / "t.db"), deadline=285)
    manager = TaskManager(settings, Spy())
    before = time.monotonic()
    task_id = await manager.submit("x", Escritorio(url="ws://h:1/x", password="p"))
    while (await manager.status(task_id)).estado == "en_curso":  # type: ignore[union-attr]
        await asyncio.sleep(0.005)
    ctx = seen[0]
    actor_s, deadline_s = settings.ACTOR_STOP_S, settings.TASK_DEADLINE_S
    assert actor_s - 0.1 <= ctx.actor_deadline - before <= actor_s + 0.5
    assert deadline_s - 0.1 <= ctx.deadline - before <= deadline_s + 0.5
    await manager.shutdown()
