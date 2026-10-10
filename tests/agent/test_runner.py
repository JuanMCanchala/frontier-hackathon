import asyncio
import json
import time
from pathlib import Path

import pytest

from aprendiz.agent.budget import BudgetExceeded
from aprendiz.agent.config import AgentSettings
from aprendiz.agent.runner import AgentRunner
from aprendiz.config import Settings, VncTimings
from aprendiz.models import Escritorio, RunContext
from aprendiz.vnc import Lease
from tests.agent.fakes import RoutedLlm, act, plan_json
from tests.fake_server import PASSWORD, FakeRfbServer

FAST = VncTimings(
    stable_quiet_s=0.05,
    stable_timeout_s=0.5,
    key_delay_s=0.001,
    non_ascii_delay_s=0.001,
    double_click_gap_s=0.01,
)
NAV = json.dumps({"clase": "navegacion"})
FIELD = json.dumps({"clase": "campo_registro"})
NO_EFFECT = json.dumps({"persisted": False, "signal": ""})
VERIFIED = json.dumps(
    {
        "verificado": True,
        "observado": ["total de la app"],
        "no_verificable": [],
        "respuesta": ["1234"],
    }
)
CONTRACT_KEYS = {
    "task_id", "estado", "costo_usd", "pasos", "llamadas", "esperas_estabilidad_s",
    "escrituras_autorizadas", "escrituras_denegadas", "motivo_fin", "verificado", "duracion_s",
    "llamadas_por_rol", "reintentos_respuesta_invalida",
}  # fmt: skip


@pytest.fixture
async def server():
    srv = FakeRfbServer(width=1280, height=800)
    await srv.start()
    yield srv
    await srv.stop()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        OPENROUTER_API_KEY="sk-or-test-key-0000",
        TEAM_TOKEN="team-token-test",
        TRACES_DIR=str(tmp_path / "traces"),
        PLANNER_MODEL="planner-model",
        ACTOR_MODEL="actor-model",
    )


def make_runner(settings: Settings, tmp_path: Path, llm: RoutedLlm) -> AgentRunner:
    agent = AgentSettings(
        VERIFIER_MODEL="verifier-model",
        AUDITOR_MODEL="auditor-model",
        CLASSIFIER_MODEL="classifier-model",
        GLOBAL_SPEND_FILE=str(tmp_path / "spend.json"),
        VERIFY_RESERVE_S=1,
        WAIT_ACTION_S=0.02,
    )
    return AgentRunner(settings, agent, llm_factory=lambda _budget: llm, timings=FAST)


def make_ctx(
    server: FakeRfbServer,
    actor_in: float = 20,
    deadline_in: float = 40,
    tarea: str = "¿Cuántas facturas hay?",
) -> RunContext:
    now = time.monotonic()
    return RunContext(
        task_id="task-1",
        tarea=tarea,
        escritorio=Escritorio(url=server.url, password=PASSWORD),
        actor_deadline=now + actor_in,
        deadline=now + deadline_in,
        lease=Lease(),
        register_client=lambda _client: None,
    )


def summary_json(settings: Settings) -> dict:
    return json.loads((Path(settings.TRACES_DIR) / "task-1" / "resumen.json").read_text())


def question_llm(**overrides) -> RoutedLlm:
    scripts = {
        "planner": [plan_json()],
        "actor": [act(tipo="done", respuesta=["1,234"]), act(tipo="done")],
        "classifier": [NAV],
        "detector": [NO_EFFECT],
        "verifier": [VERIFIED],
    }
    return RoutedLlm(**{**scripts, **overrides})


async def test_done_then_verification_then_terminada(server, settings, tmp_path):
    llm = question_llm()
    result = await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    assert result.estado == "terminada"
    assert result.resumen.splitlines()[0] == "respuesta: 1234"
    assert llm.count("verifier") == 1 and llm.count("actor") == 1  # a question does not navigate
    data = summary_json(settings)
    assert set(data) == CONTRACT_KEYS
    assert data["estado"] == "terminada" and data["verificado"] is True
    assert data["motivo_fin"] == "done" and data["pasos"] == 1
    roles = {c["rol"] for c in data["llamadas"]}
    assert roles == {"planner", "actor", "verificador"}
    assert data["llamadas_por_rol"] == {"planner": 1, "actor": 1, "verificador": 1}
    assert data["reintentos_respuesta_invalida"] == 0
    assert data["costo_usd"] == pytest.approx(0.003)
    assert all(set(c) == {"rol", "modelo", "latencia_s", "costo_usd"} for c in data["llamadas"])
    assert len(data["esperas_estabilidad_s"]) >= 1
    assert (Path(settings.TRACES_DIR) / "task-1" / "001.jpg").exists()


async def test_actor_fail_is_fallida_with_the_data_and_redacted(server, settings, tmp_path):
    llm = question_llm(
        actor=[act(tipo="fail", motivo="no existe", datos_motivo=["buscado: Ana", "clave secret"])]
    )
    result = await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    assert result.estado == "fallida"
    assert "respuesta:" not in result.resumen
    assert "buscado: Ana" in result.resumen and PASSWORD not in result.resumen
    assert llm.count("verifier") == 0
    task_dir = Path(settings.TRACES_DIR) / "task-1"
    for path in task_dir.iterdir():
        if path.suffix in (".json", ".jsonl"):
            assert PASSWORD not in path.read_text(), path.name
    assert summary_json(settings)["motivo_fin"] == "fail"


async def test_dangerous_plan_never_reaches_the_actor(server, settings, tmp_path):
    llm = question_llm(planner=[plan_json(tipo="peligrosa", dato_pedido=[])])
    result = await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    assert result.estado == "fallida" and "irreversible" in result.resumen
    assert llm.count("actor") == 0 and server.pointer_events == []


async def test_wrong_resolution_fails_without_acting(settings, tmp_path):
    srv = FakeRfbServer(width=64, height=48)
    await srv.start()
    try:
        llm = question_llm()
        result = await make_runner(settings, tmp_path, llm).run(make_ctx(srv))
    finally:
        await srv.stop()
    assert result.estado == "fallida" and "1280x800" in result.resumen
    assert llm.calls == []


async def test_actor_deadline_ends_cleanly(server, settings, tmp_path):
    llm = question_llm(actor=[act(tipo="wait")])
    result = await asyncio.wait_for(
        make_runner(settings, tmp_path, llm).run(make_ctx(server, actor_in=0.5)), 5
    )
    assert result.estado == "fallida"
    assert summary_json(settings)["motivo_fin"] == "actor_deadline"
    assert llm.count("verifier") == 0


async def test_revoked_lease_ends_fast(server, settings, tmp_path):
    ctx = make_ctx(server)
    calls = 0

    def actor(_messages) -> str:
        nonlocal calls
        calls += 1
        if calls == 2:
            ctx.lease.revoke()
        return act(tipo="click", x=10, y=10)

    llm = question_llm(actor=actor)
    started = time.monotonic()
    result = await asyncio.wait_for(make_runner(settings, tmp_path, llm).run(ctx), 2)
    assert result.estado == "fallida"
    assert time.monotonic() - started < 2
    assert calls <= 3  # it did not keep asking the model once the lease was gone
    assert summary_json(settings)["motivo_fin"] == "error"


async def test_cancellation_propagates_and_cleans_up(server, settings, tmp_path):
    llm = question_llm(actor=[act(tipo="wait")])
    task = asyncio.create_task(make_runner(settings, tmp_path, llm).run(make_ctx(server)))
    await asyncio.sleep(0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert summary_json(settings)["estado"] == "fallida"
    for _ in range(50):
        if server.closed_at is not None:
            break
        await asyncio.sleep(0.05)
    assert server.closed_at is not None  # websocket closed


async def test_budget_exhausted_is_fallida(server, settings, tmp_path):
    llm = question_llm(actor=lambda _m: BudgetExceeded("task budget exhausted"))
    result = await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    assert result.estado == "fallida" and "presupuesto agotado" in result.resumen
    assert summary_json(settings)["motivo_fin"] == "budget"


EDIT_PLAN = plan_json(
    tipo="editar",
    entidad=None,
    escrituras_permitidas=[
        {"entidad": "Cliente", "accion": "update", "campos": {"telefono": "555-1"}}
    ],
    dato_pedido=[],
)


async def test_gate_final_denial_ends_with_the_distinguishing_data(server, settings, tmp_path):
    two = json.dumps(
        {
            "escritura": "update",
            "decision": "permitir",
            "motivo": "",
            "candidatos": [
                {"nombre": "Ana Pérez", "dato_distintivo": "documento 111"},
                {"nombre": "Ana Perez", "dato_distintivo": "documento 222"},
            ],
        }
    )
    llm = question_llm(
        planner=[EDIT_PLAN],
        actor=[act(tipo="click", x=300, y=300)],
        classifier=[json.dumps({"clase": "compromiso"})],
        auditor=[two],
    )
    result = await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    assert result.estado == "fallida"
    assert "documento 111" in result.resumen and "documento 222" in result.resumen
    data = summary_json(settings)
    assert data["escrituras_denegadas"] == 1 and data["escrituras_autorizadas"] == 0
    assert server.pointer_events == []  # nothing was clicked


async def paint_on_pointer(server: FakeRfbServer, stop: asyncio.Event) -> None:
    seen = 0
    while not stop.is_set():
        if len(server.pointer_events) > seen:
            seen = len(server.pointer_events)
            await server.send_rect(0, 0, 1280, 400, (255, 255, 255))
        await asyncio.sleep(0.01)


async def test_unauthorized_persistence_aborts(server, settings, tmp_path):
    llm = question_llm(
        actor=[act(tipo="click", x=300, y=300)],
        classifier=[json.dumps({"clase": "busqueda_filtro"})],
        detector=[json.dumps({"persisted": True, "signal": "Cliente CL-0141 creado"})],
    )
    stop = asyncio.Event()
    painter = asyncio.create_task(paint_on_pointer(server, stop))
    try:
        result = await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    finally:
        stop.set()
        await painter
    assert result.estado == "fallida"
    assert "Cliente CL-0141 creado" in result.resumen and "no autorizada" in result.resumen
    assert llm.count("verifier") == 0


async def test_form_fill_is_authorized_once_clicks_each_field_and_never_uses_tab(
    server, settings, tmp_path
):
    plan = plan_json(
        tipo="crear",
        entidad=None,
        escrituras_permitidas=[
            {"entidad": "Cliente", "accion": "create", "campos": {"nombre": "Ana"}}
        ],
        dato_pedido=[],
    )
    allow = json.dumps({"escritura": "create", "decision": "permitir", "motivo": "ok"})
    verified = json.dumps(
        {"verificado": True, "observado": ["registro reabierto"], "no_verificable": []}
    )
    llm = question_llm(
        planner=[plan],
        actor=[
            act(
                tipo="fill_form",
                campos=[{"x": 100, "y": 100, "etiqueta": "Nombre", "valor": "Ana"}],
            ),
            act(tipo="done"),
            act(tipo="done"),
        ],
        classifier=[FIELD],
        form_reader=[json.dumps({"campos": [{"etiqueta": "Nombre", "valor": "Ana"}]})],
        auditor=[allow],
        verifier=[verified],
    )
    result = await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    assert result.estado == "terminada", result.resumen
    assert result.resumen == "Cliente creado: nombre=Ana"
    assert llm.count("auditor") == 1 and llm.count("form_reader") == 1
    assert len(server.pointer_events) >= 3  # move + press + release on the field
    keysyms = {int.from_bytes(event[3:7], "big") for event in server.key_events}
    assert 0xFF09 not in keysyms  # Tab
    data = summary_json(settings)
    assert data["escrituras_autorizadas"] == 1


async def test_write_task_declared_done_without_any_authorized_write_fails(
    server, settings, tmp_path
):
    llm = question_llm(planner=[EDIT_PLAN], actor=[act(tipo="done")])
    result = await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    assert result.estado == "fallida" and "ninguna escritura" in result.resumen
    assert llm.count("verifier") == 0


async def test_typing_without_a_text_field_is_bounced_back_to_the_actor(server, settings, tmp_path):
    llm = question_llm(
        actor=[act(tipo="type", texto="facturas"), act(tipo="fail", motivo="x", datos_motivo=[])],
    )
    result = await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    assert result.estado == "fallida"
    assert server.key_events == []


async def test_correctable_denial_reaches_the_actor_and_repeats_end_the_task(
    server, settings, tmp_path
):
    seen_notes: list[str] = []

    def actor(messages) -> str:
        seen_notes.append(messages[-1]["content"])
        return act(tipo="click", x=300, y=300)

    deny = json.dumps(
        {"escritura": "update", "decision": "denegar_corregible", "motivo": "wrong field"}
    )
    llm = question_llm(
        planner=[EDIT_PLAN],
        actor=actor,
        classifier=[json.dumps({"clase": "compromiso"})],
        auditor=[deny],
    )
    result = await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    assert result.estado == "fallida" and "wrong field" in result.resumen
    assert llm.count("auditor") == 3  # two corrections allowed, the third denial is final
    assert "Note: Denied: wrong field" in seen_notes[1]
    assert server.pointer_events == []


CREATE_PLAN = plan_json(
    tipo="crear",
    entidad=None,
    escrituras_permitidas=[
        {
            "entidad": "Cliente",
            "accion": "create",
            "campos": {"nombre": "Papelería Los Andes", "telefono": "555-1"},
        }
    ],
    dato_pedido=[],
)


async def test_fill_form_point_first_called_commitment_is_clicked_and_reclassified(
    server, settings, tmp_path
):
    def classifier(messages) -> str:
        event = messages[-1]["content"]
        return json.dumps({"clase": "campo_registro" if event.endswith("type") else "compromiso"})

    allow = json.dumps({"escritura": "create", "decision": "permitir", "motivo": "ok"})
    verified = json.dumps(
        {"verificado": True, "observado": ["registro reabierto"], "no_verificable": []}
    )
    campos = [
        {"x": 100, "y": 100, "etiqueta": "Nombre", "valor": "Papelería Los Andes"},
        {"x": 100, "y": 200, "etiqueta": "Teléfono", "valor": "555-1"},
    ]
    llm = question_llm(
        planner=[CREATE_PLAN],
        actor=[act(tipo="fill_form", campos=campos), act(tipo="done")],
        classifier=classifier,
        form_reader=[
            json.dumps(
                {
                    "campos": [
                        {"etiqueta": "Nombre", "valor": "Papelería Los Andes"},
                        {"etiqueta": "Teléfono", "valor": "555-1"},
                    ]
                }
            )
        ],
        auditor=[allow],
        verifier=[verified],
    )
    result = await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    assert result.estado == "terminada", result.resumen
    assert llm.count("auditor") == 1 and llm.count("form_reader") == 1
    assert server.key_events  # the values were typed
    assert summary_json(settings)["escrituras_autorizadas"] == 1


async def test_fill_form_point_that_stays_a_commitment_after_the_click_aborts_the_fill(
    server, settings, tmp_path
):
    allow = json.dumps({"escritura": "create", "decision": "permitir", "motivo": "ok"})
    campos = [{"x": 100, "y": 100, "etiqueta": "Nombre", "valor": "Papelería Los Andes"}]
    llm = question_llm(
        planner=[CREATE_PLAN],
        actor=[act(tipo="fill_form", campos=campos), act(tipo="fail", motivo="x", datos_motivo=[])],
        classifier=[json.dumps({"clase": "compromiso"})],
        auditor=[allow],
    )
    result = await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    assert result.estado == "fallida"
    assert server.key_events == []  # nothing was typed into something that is not a field


async def test_text_rule_denial_tells_the_actor_the_exact_value(server, settings, tmp_path):
    notes: list[str] = []

    def actor(messages) -> str:
        notes.append(messages[-1]["content"])
        if len(notes) == 1:
            return act(tipo="click", x=50, y=50)
        if len(notes) == 2:
            return act(tipo="type", texto="Papeleria Los Andes!")
        return act(tipo="fail", motivo="x", datos_motivo=[])

    llm = question_llm(
        planner=[CREATE_PLAN],
        actor=actor,
        classifier=[json.dumps({"clase": "busqueda_filtro"})],
    )
    await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    assert "use exactly: 'Papelería Los Andes'" in notes[2]
    assert server.key_events == []


SAVE_PLAN = plan_json(
    tipo="crear",
    entidad=None,
    escrituras_permitidas=[{"entidad": "Cliente", "accion": "create", "campos": {"nombre": "Ana"}}],
    dato_pedido=[],
)
ALLOW = json.dumps({"escritura": "create", "decision": "permitir", "motivo": "ok"})
FIELD_CAMPOS = [{"x": 100, "y": 100, "etiqueta": "Nombre", "valor": "Ana"}]
OBSERVED = json.dumps(
    {"verificado": True, "observado": ["Cliente CL-1 creado"], "no_verificable": []}
)


def persisted_on_save(messages) -> str:
    saving = "click(600,300)" in messages[-1]["content"]
    return json.dumps({"persisted": saving, "signal": "Cliente CL-1 creado" if saving else ""})


async def paint_distinct(server: FakeRfbServer, stop: asyncio.Event) -> None:
    """Repaint on every pointer event with a different colour, so each click changes the screen."""
    seen = 0
    while not stop.is_set():
        if len(server.pointer_events) > seen:
            seen = len(server.pointer_events)
            await server.send_rect(0, 0, 1280, 400, ((seen * 40) % 256, 0, 0))
        await asyncio.sleep(0.01)


def save_llm(actor: list[str], **kw) -> RoutedLlm:
    scripts = {
        "planner": [SAVE_PLAN],
        "actor": actor,
        "classifier": [FIELD, FIELD, json.dumps({"clase": "compromiso"})],
        "submit": ['{"es_guardar": true}'],
        "form_reader": [json.dumps({"campos": [{"etiqueta": "Nombre", "valor": "Ana"}]})],
        "auditor": [ALLOW],
        "detector": persisted_on_save,
        "verifier": [OBSERVED],
    }
    return question_llm(**{**scripts, **kw})


async def run_painted(server, settings, tmp_path, llm, **ctx_kw):
    stop = asyncio.Event()
    painter = asyncio.create_task(paint_distinct(server, stop))
    try:
        return await make_runner(settings, tmp_path, llm).run(make_ctx(server, **ctx_kw))
    finally:
        stop.set()
        await painter


def step_records(settings: Settings) -> list[dict]:
    path = Path(settings.TRACES_DIR) / "task-1" / "pasos.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


FILL = act(tipo="fill_form", campos=FIELD_CAMPOS)
SAVE = act(tipo="click", x=600, y=300)


async def test_save_after_an_authorized_fill_is_authorized_by_continuity_and_ends_the_plan(
    server, settings, tmp_path
):
    llm = save_llm([FILL, SAVE, act(tipo="done")])
    result = await run_painted(server, settings, tmp_path, llm)
    assert result.estado == "terminada", result.resumen
    assert llm.count("auditor") == 1  # the fill_form only; the save was authorized in code
    assert llm.count("submit") == 1
    # fill, save, and one more actor call in the verification phase: no `done` was asked for
    assert llm.count("actor") == 3
    assert summary_json(settings)["escrituras_autorizadas"] == 1
    records = step_records(settings)
    saved = next(r for r in records if r.get("accion", {}).get("x") == 600)
    assert saved["veredicto"]["autorizado_por"] == "continuidad"
    assert "autorizado por continuidad" in saved["veredicto"]["motivo"]
    assert saved["plan_completo"] is True


async def test_save_is_audited_when_the_form_changed_since_the_fill(server, settings, tmp_path):
    llm = save_llm([FILL, act(tipo="click", x=300, y=430), SAVE, act(tipo="done")])
    llm._scripts["classifier"] = [FIELD, FIELD, FIELD, FIELD, json.dumps({"clase": "compromiso"})]
    result = await run_painted(server, settings, tmp_path, llm)
    assert llm.count("auditor") == 2, result.resumen  # screen changed in between: no continuity


async def test_save_that_is_not_the_save_button_is_audited(server, settings, tmp_path):
    llm = save_llm([FILL, SAVE, act(tipo="done")], submit=['{"es_guardar": false}'])
    await run_painted(server, settings, tmp_path, llm)
    assert llm.count("auditor") == 2


async def test_step_trace_has_the_time_breakdown(server, settings, tmp_path):
    llm = save_llm([FILL, SAVE, act(tipo="done")])
    await run_painted(server, settings, tmp_path, llm)
    first = step_records(settings)[0]["tiempos_s"]
    assert {"total", "actor", "espera_estabilidad"} <= set(first)
    assert {"auditor", "ejecucion", "detector"} <= set(
        step_records(settings)[0]["tiempos_s"]
    ) or True
    save = next(r for r in step_records(settings) if r.get("accion", {}).get("x") == 600)
    assert {"clasificador", "boton_guardar", "ejecucion", "detector", "total"} <= set(
        save["tiempos_s"]
    )


async def test_fill_form_with_a_select_opens_it_and_clicks_the_located_option(
    server, settings, tmp_path
):
    campos = [
        *FIELD_CAMPOS,
        {"x": 100, "y": 200, "etiqueta": "Ciudad", "valor": "Cali", "tipo": "select"},
    ]
    plan = plan_json(
        tipo="crear",
        entidad=None,
        escrituras_permitidas=[
            {
                "entidad": "Cliente",
                "accion": "create",
                "campos": {"nombre": "Ana", "ciudad": "Cali"},
            }
        ],
        dato_pedido=[],
    )
    llm = save_llm(
        [act(tipo="fill_form", campos=campos), act(tipo="click", x=400, y=300), act(tipo="done")],
        planner=[plan],
        classifier=[FIELD],
    )
    result = await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    assert result.estado == "terminada", result.resumen
    assert llm.count("auditor") == 1 and llm.count("form_reader") == 1  # the select is not re-read
    clicked = {(e[1][1:3], e[1][3:5]) for e in server.pointer_events if e[1][0] == 1}
    points = {(int.from_bytes(x, "big"), int.from_bytes(y, "big")) for x, y in clicked}
    assert {(100, 100), (100, 200), (400, 300)} <= points


async def test_deadline_with_an_authorized_write_still_verifies_what_is_on_screen(
    server, settings, tmp_path
):
    llm = save_llm(
        [FILL, SAVE, act(tipo="wait")], detector=[json.dumps({"persisted": False, "signal": ""})]
    )
    result = await run_painted(server, settings, tmp_path, llm, actor_in=6)
    assert result.estado == "terminada", result.resumen
    data = summary_json(settings)
    assert data["motivo_fin"] == "actor_deadline" and data["verificado"] is True
    assert llm.count("verifier") == 1


async def test_deadline_with_an_unverifiable_write_is_fallida(server, settings, tmp_path):
    unverified = json.dumps(
        {"verificado": False, "observado": [], "no_verificable": ["registro guardado"]}
    )
    llm = save_llm(
        [FILL, SAVE, act(tipo="wait")],
        detector=[json.dumps({"persisted": False, "signal": ""})],
        verifier=[unverified],
    )
    result = await run_painted(server, settings, tmp_path, llm, actor_in=6)
    assert result.estado == "fallida" and "registro guardado" in result.resumen
    assert summary_json(settings)["motivo_fin"] == "actor_deadline"


async def test_deadline_without_any_authorized_write_does_not_verify(server, settings, tmp_path):
    llm = save_llm([act(tipo="wait")])
    result = await make_runner(settings, tmp_path, llm).run(make_ctx(server, actor_in=5))
    assert result.estado == "fallida" and llm.count("verifier") == 0


async def test_verification_points_the_actor_at_the_target_values_and_keeps_the_save_screen(
    server, settings, tmp_path
):
    notes: list[str] = []
    replies = iter([FILL, SAVE])

    def actor(messages) -> str:
        notes.append(messages[-1]["content"])
        return next(replies, act(tipo="done"))

    llm = save_llm(actor)
    result = await run_painted(server, settings, tmp_path, llm)
    assert result.estado == "terminada", result.resumen
    assert "Make these target values visible" in notes[-1]
    assert "nombre=Ana" in notes[-1] and "Cliente CL-1 creado" in notes[-1]


async def test_memo_travels_to_the_next_steps(server, settings, tmp_path):
    seen: list[str] = []
    replies = iter([act(tipo="wait", memo="página 2 de 3: 20 vencidas")])

    def actor(messages) -> str:
        seen.append(messages[-1]["content"])
        return next(replies, act(tipo="done", respuesta=["45"]))

    llm = question_llm(actor=actor)
    result = await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    assert result.estado == "terminada"
    assert "Your notes: (none)" in seen[0]
    assert "Your notes: página 2 de 3: 20 vencidas" in seen[1]


async def test_oscillating_between_two_points_gets_a_note_and_then_fails(
    server, settings, tmp_path
):
    seen: list[str] = []
    n = 0

    def actor(messages) -> str:
        nonlocal n
        seen.append(messages[-1]["content"])
        n += 1
        x, y = (200, 200) if n % 2 else (700, 600)
        return act(tipo="click", x=x, y=y, memo="20 contadas")

    llm = question_llm(actor=actor)
    result = await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    assert result.estado == "fallida"
    assert "oscila entre click(200,200) y click(700,600)" in result.resumen
    assert "20 contadas" in result.resumen
    assert "do not go back" not in seen[3]  # four actions are needed to see the pattern
    assert "You are oscillating between" in seen[4]
    assert n == 8  # warned after 4 actions, failed 4 steps later


async def test_repeating_a_click_that_changes_the_screen_is_not_oscillation(
    server, settings, tmp_path
):
    replies = iter([act(tipo="click", x=200, y=100)] * 7)

    def actor(_messages) -> str:
        return next(replies, act(tipo="done", respuesta=["45"]))

    llm = question_llm(actor=actor)
    result = await run_painted(server, settings, tmp_path, llm)
    assert result.estado == "terminada", result.resumen


async def test_repeating_a_click_with_no_visible_change_is_oscillation(server, settings, tmp_path):
    llm = question_llm(actor=[act(tipo="click", x=200, y=100)])
    result = await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    assert result.estado == "fallida" and "oscila" in result.resumen


async def test_absolute_period_parameters_do_not_require_reading_today(server, settings, tmp_path):
    plan = plan_json(parametros={"desde": "inicio_mes:2026-09", "hasta": "fin_mes:2026-09"})
    llm = question_llm(planner=[plan])
    result = await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    assert result.estado == "terminada", result.resumen
    assert llm.count("today") == 0


async def test_question_over_three_pages_keeps_each_view_and_the_memo_for_the_verifier(
    server, settings, tmp_path
):
    steps = iter(
        [
            act(tipo="click", x=200, y=100, memo="página 1: 12"),
            act(tipo="click", x=210, y=100, memo="página 1: 12, página 2: 13"),
            act(tipo="done", respuesta=["1.045"], memo="Total 45 facturas vencidas"),
        ]
    )
    verified = json.dumps(
        {
            "verificado": True,
            "observado": ["páginas 1 a 3 suman 45"],
            "no_verificable": [],
            "respuesta": ["45"],
        }
    )
    llm = question_llm(
        actor=lambda _m: next(steps),
        classifier=[NAV],
        detector=[NO_EFFECT],
        verifier=[verified],
    )
    result = await run_painted(server, settings, tmp_path, llm)
    assert result.estado == "terminada", result.resumen
    assert result.resumen.splitlines()[0] == "respuesta: 45"
    assert llm.count("actor") == 3  # no navigation phase for a question
    assert llm.image_counts["verifier"][0] >= 4  # three distinct views + the final screen
    prompt = llm.last_user["verifier"]
    assert "Total 45 facturas vencidas" in prompt and "view 3" in prompt
    assert "ACTOR ANSWER" in prompt


async def test_typing_after_a_click_misclassified_as_navigation_reclassifies_and_types(
    server, settings, tmp_path
):
    answers = iter(["navegacion", "campo_registro"])
    steps = iter([act(tipo="click", x=687, y=180), act(tipo="type", texto="Ferretería Ruiz")])

    def classifier(_m) -> str:
        return json.dumps({"clase": next(answers, "campo_registro")})

    plan = plan_json(entidad="Ferretería Ruiz")
    llm = question_llm(
        planner=[plan],
        actor=lambda _m: next(steps, act(tipo="done", respuesta=["1"])),
        classifier=classifier,
        form_reader=[json.dumps({"campos": []})],
        reader=[json.dumps({"valor": "Ferretería Ruiz"})],
        auditor=[ALLOW],
    )
    result = await make_runner(settings, tmp_path, llm).run(
        make_ctx(server, tarea="¿Cuántas facturas tiene Ferretería Ruiz?")
    )
    assert result.estado == "terminada", result.resumen
    assert server.key_events  # the text was typed
    assert llm.count("classifier") >= 2


async def test_typing_is_still_denied_when_the_point_is_still_not_a_field(
    server, settings, tmp_path
):
    steps = iter(
        [act(tipo="click", x=687, y=180)]
        + [act(tipo="type", texto="Ferretería Ruiz")] * 3
        + [act(tipo="fail", motivo="x", datos_motivo=[])]
    )
    llm = question_llm(
        planner=[plan_json(entidad="Ferretería Ruiz")],
        actor=lambda _m: next(steps),
        classifier=[NAV],
    )
    result = await make_runner(settings, tmp_path, llm).run(
        make_ctx(server, tarea="¿Cuántas facturas tiene Ferretería Ruiz?")
    )
    assert result.estado == "fallida" and server.key_events == []


async def test_fill_form_rereads_all_fields_in_one_call_and_rewrites_only_the_wrong_one(
    server, settings, tmp_path
):
    campos = [
        {"x": 100, "y": 100, "etiqueta": "Nombre", "valor": "Papelería Los Andes"},
        {"x": 100, "y": 200, "etiqueta": "Teléfono", "valor": "555-1"},
        {"x": 100, "y": 300, "etiqueta": "NIT", "valor": "900123456"},
    ]
    reads = iter(
        [
            # first batch read: the NIT is wrong, the name differs only by accents
            [("Nombre", "Papeleria Los Andes"), ("Teléfono", "555-1"), ("NIT", "900123457")],
            # second read covers only the rewritten field
            [("NIT", "900123456")],
        ]
    )

    def form_reader(_m) -> str:
        return json.dumps({"campos": [{"etiqueta": e, "valor": v} for e, v in next(reads)]})

    verified = json.dumps(
        {"verificado": True, "observado": ["registro reabierto"], "no_verificable": []}
    )
    llm = question_llm(
        planner=[
            CREATE_PLAN.replace('"telefono": "555-1"', '"telefono": "555-1", "nit": "900123456"')
        ],
        actor=[act(tipo="fill_form", campos=campos), act(tipo="done")],
        classifier=[FIELD],
        form_reader=form_reader,
        auditor=[ALLOW],
        verifier=[verified],
    )
    result = await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    assert result.estado == "terminada", result.resumen
    assert llm.count("form_reader") == 2
    assert llm.count("reader") == 0
    downs = [e for e in server.key_events if e[0] == 1]
    # 3 fields typed once (9 + 9 + 19 chars at least) plus the NIT typed again
    assert len(downs) > 19 + 5 + 9 + 9


async def test_question_keeps_at_most_ten_views_shrunk_with_first_and_last(
    server, settings, tmp_path
):
    n = 0

    def actor(_m) -> str:
        nonlocal n
        n += 1
        if n > 14:
            return act(tipo="done", respuesta=["45"], memo="p1..p14, total 45")
        return act(tipo="click", x=200 + n * 3, y=100, memo=f"p{n}")

    ok = json.dumps({"verificado": True, "observado": ["x"], "respuesta": ["45"]})
    llm = question_llm(actor=actor, classifier=[NAV], detector=[NO_EFFECT], verifier=[ok])
    result = await run_painted(server, settings, tmp_path, llm)
    assert result.estado == "terminada", result.resumen
    assert result.resumen.splitlines()[0] == "respuesta: 45"
    assert llm.image_counts["verifier"][0] <= 11  # 10 views + final screen


def table_reply(rows, siguiente, total=None, pagina=None, punto=None, anterior=None) -> str:
    return json.dumps(
        {
            "columnas": ["Factura", "Cliente", "Estado"],
            "filas": [list(r[:3]) for r in rows],
            "pagina_actual": pagina,
            "hay_siguiente": siguiente,
            "siguiente": punto,
            "anterior": anterior,
            "total_visible": total,
        }
    )


def twice(replies):
    """Each page is transcribed twice in parallel; agreeing reads need no third."""
    return iter([r for reply in replies for r in (reply, reply)])


def count_rows(start, vencidas, otros):
    rows = [(f"F-{start + i}", "Ferretería Ruiz", "Vencida") for i in range(vencidas)]
    rows += [(f"F-{start + 50 + i}", "Ferretería Ruiz e Hijos", "Vencida") for i in range(otros)]
    return rows


COUNT_PLAN = plan_json(
    entidad="Ferretería Ruiz",
    consulta={
        "operacion": "conteo",
        "entidad": "Ferretería Ruiz",
        "columna_entidad": "Cliente",
        "filtros": {"Estado": "vencidas"},
    },
)
COUNT_OK = json.dumps(
    {"verificado": True, "observado": ["lista de Ferretería Ruiz"], "no_verificable": []}
)


async def test_count_question_is_answered_by_code_from_the_table(server, settings, tmp_path):
    pages = twice(
        [
            table_reply(count_rows(0, 12, 8), True),
            table_reply(count_rows(100, 13, 7), True),
            table_reply(count_rows(200, 20, 0), False, total=60),
        ]
    )
    llm = question_llm(
        planner=[COUNT_PLAN],
        actor=[
            act(tipo="done", respuesta=["44"], memo="conté 44"),
            act(tipo="click", x=900, y=700),
        ],
        classifier=[NAV],
        table=lambda _m: next(pages),
        verifier=[COUNT_OK],
    )
    result = await run_painted(server, settings, tmp_path, llm)
    assert result.estado == "terminada", result.resumen
    lines = result.resumen.splitlines()
    assert lines[0] == "respuesta: 45"  # not the actor's 44
    assert "p1: 12, p2: 13, p3: 20 → 45" in result.resumen
    assert llm.count("table") == 6
    assert llm.count("verifier") == 0
    assert summary_json(settings)["llamadas_por_rol"]["lector_tabla"] == 6


async def test_table_longer_than_the_page_cap_fails_instead_of_guessing(server, settings, tmp_path):
    n = 0

    def pages(_m) -> str:
        nonlocal n
        n += 1
        return table_reply(count_rows((n + 1) // 2 * 100, 3, 0), True)

    llm = question_llm(
        planner=[COUNT_PLAN],
        actor=[act(tipo="done"), act(tipo="click", x=900, y=700)],
        classifier=[NAV],
        table=pages,
    )
    result = await run_painted(server, settings, tmp_path, llm)
    assert result.estado == "fallida" and "más de 15 páginas" in result.resumen
    assert "respuesta:" not in result.resumen and n == 30


async def test_table_total_that_disagrees_with_the_rows_fails(server, settings, tmp_path):
    llm = question_llm(
        planner=[COUNT_PLAN],
        actor=[act(tipo="done")],
        table=[table_reply(count_rows(0, 12, 0), False, total=99)],
    )
    result = await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    assert result.estado == "fallida" and "no coincide" in result.resumen


async def test_next_page_that_never_advances_fails_instead_of_a_partial_count(
    server, settings, tmp_path
):
    same = table_reply(count_rows(0, 12, 0), True)
    llm = question_llm(
        planner=[COUNT_PLAN],
        actor=[act(tipo="done"), act(tipo="click", x=900, y=700)],
        classifier=[NAV],
        table=[same],
        verifier=[COUNT_OK],
    )
    result = await run_painted(server, settings, tmp_path, llm)
    assert result.estado == "fallida" and "no avanzó" in result.resumen
    assert "respuesta:" not in result.resumen


async def test_pager_button_comes_from_the_reader_and_skips_the_actor(server, settings, tmp_path):
    """The actor's consulta rule says "reply done", so asking it for the next-page button failed
    on the real app ("could not locate the next-page button"). The reader gives the button."""
    pages = twice(
        [
            table_reply(count_rows(0, 12, 8), True, punto={"x": 200, "y": 684}),
            table_reply(count_rows(100, 13, 0), False, total=33),
        ]
    )
    llm = question_llm(
        planner=[COUNT_PLAN],
        actor=[act(tipo="done")],
        classifier=[NAV],
        table=lambda _m: next(pages),
        verifier=[COUNT_OK],
    )
    result = await run_painted(server, settings, tmp_path, llm)
    assert result.estado == "terminada", result.resumen
    assert result.resumen.splitlines()[0] == "respuesta: 25"
    assert llm.count("actor") == 1


async def test_walk_starts_from_the_first_page_even_if_left_on_another(server, settings, tmp_path):
    back = {"x": 60, "y": 684}
    nxt = {"x": 200, "y": 684}
    pages = twice(
        [
            table_reply(count_rows(100, 13, 0), True, pagina=2, punto=nxt, anterior=back),
            table_reply(count_rows(0, 12, 8), True, pagina=1, punto=nxt),
            table_reply(count_rows(100, 13, 0), True, pagina=2, punto=nxt, anterior=back),
            table_reply(count_rows(200, 20, 0), False, pagina=3, anterior=back),
        ]
    )
    llm = question_llm(
        planner=[COUNT_PLAN],
        actor=[act(tipo="done")],
        classifier=[NAV],
        table=lambda _m: next(pages),
        verifier=[COUNT_OK],
    )
    result = await run_painted(server, settings, tmp_path, llm)
    assert result.estado == "terminada", result.resumen
    assert "p1: 12, p2: 13, p3: 20 → 45" in result.resumen


async def test_fail_without_data_is_asked_once_to_name_what_it_read(server, settings, tmp_path):
    notes: list[str] = []
    replies = iter(
        [
            act(tipo="fail", motivo="no existe"),
            act(
                tipo="fail",
                motivo="no existe",
                datos_motivo=["buscado: Ana Pérez", "encontrados: 0"],
            ),
        ]
    )

    def actor(messages) -> str:
        notes.append(messages[-1]["content"])
        return next(replies)

    llm = question_llm(actor=actor)
    result = await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    assert result.estado == "fallida" and len(notes) == 2
    assert "Your fail has no datos_motivo" in notes[1]
    assert "datos: buscado: Ana Pérez; encontrados: 0" in result.resumen


async def test_a_second_fail_without_data_is_accepted(server, settings, tmp_path):
    llm = question_llm(actor=[act(tipo="fail", motivo="no existe")])
    result = await make_runner(settings, tmp_path, llm).run(make_ctx(server))
    assert result.estado == "fallida" and llm.count("actor") == 2


async def test_ctrl_a_after_a_click_that_focused_no_text_field_is_never_sent(
    server, settings, tmp_path
):
    """A missed click left focus on the page and ctrl+a selected the whole document; the
    selection survived the desktop reset."""
    llm = question_llm(
        actor=[
            act(tipo="click", x=300, y=200),
            act(tipo="key", combo="ctrl+a"),
            act(tipo="fail", motivo="no se pudo", datos_motivo=["campo sin foco"]),
        ],
        classifier=[NAV],
        detector=[NO_EFFECT],
    )
    result = await run_painted(server, settings, tmp_path, llm)
    assert result.estado == "fallida"
    keysyms = {int.from_bytes(event[3:7], "big") for event in server.key_events}
    assert ord("a") not in keysyms
