import json

from PIL import Image

from aprendiz.agent.gate import EvidenceContext, GateState, authorize
from aprendiz.agent.llm import LlmError, ModerationBlocked
from aprendiz.agent.schemas import Action, Plan
from tests.agent.fakes import FakeLlm

IMG = Image.new("RGB", (8, 8))
CLICK = Action(tipo="click", x=10, y=10)

UPDATE_PLAN = Plan(
    tipo="editar",
    entidad="Ana Pérez",
    escrituras_permitidas=[
        {"entidad": "Cliente", "accion": "update", "campos": {"telefono": "555-1"}}
    ],
)
EXPORT_PLAN = Plan(
    tipo="exportar",
    escrituras_permitidas=[{"entidad": "Reporte", "accion": "export", "campos": {}}],
)


def ev(search=True, **kw) -> EvidenceContext:
    return EvidenceContext(IMG, capturas_busqueda=[IMG] if search else [], **kw)


def reply(**kw) -> str:
    base = {"escritura": "update", "decision": "permitir", "motivo": "ok"}
    return json.dumps({**base, **kw})


async def run(llm, plan=UPDATE_PLAN, accion=CLICK, evidencia=None, state=None):
    return await authorize(
        llm, plan, accion, evidencia or ev(), model="aud", state=state or GateState()
    )


async def test_allowed_update_sends_all_images():
    llm = FakeLlm([reply(candidatos=[{"nombre": "Ana Pérez", "dato_distintivo": "doc 1"}])])
    evidencia = ev(captura_formulario_inicial=IMG)
    verdict = await run(llm, evidencia=evidencia)
    assert verdict.decision == "permitir"
    assert len(llm.calls[0]["images"]) == 3
    assert llm.calls[0]["model"] == "aud"


async def test_typing_the_entity_name_is_allowed_as_a_search():
    verdict = await run(FakeLlm([]), accion=Action(tipo="type", texto="Ana Pérez"))
    assert verdict.decision == "permitir"


async def test_plan_without_writes_denies_without_calling_model():
    llm = FakeLlm([])
    verdict = await run(llm, plan=Plan(tipo="preguntar", entidad="Ana"))
    assert verdict.decision == "denegar_corregible" and llm.calls == []


async def test_write_outside_plan_is_denied_even_if_auditor_allows():
    verdict = await run(FakeLlm([reply(escritura="delete")]))
    assert verdict.decision == "denegar_corregible"
    assert "delete" in verdict.motivo


async def test_typed_value_not_in_plan_is_denied_before_the_model():
    llm = FakeLlm([])
    action = Action(tipo="fill_form", campos=[{"x": 1, "y": 1, "etiqueta": "Tel", "valor": "999"}])
    verdict = await run(llm, accion=action)
    assert verdict.decision == "denegar_corregible" and llm.calls == []


async def test_fill_form_with_plan_value_goes_to_auditor():
    action = Action(
        tipo="fill_form", campos=[{"x": 1, "y": 1, "etiqueta": "Tel", "valor": "555-1"}]
    )
    llm = FakeLlm([reply()])
    assert (await run(llm, accion=action)).decision == "permitir"


async def test_resolved_parameter_counts_as_plan_value():
    plan = Plan(
        tipo="editar",
        entidad="Ana",
        escrituras_permitidas=[
            {"entidad": "Cita", "accion": "update", "campos": {"fecha": "manana"}}
        ],
        parametros={"manana": "hoy_app+1"},
    )
    action = Action(tipo="type", texto="11/10/2026")
    evidencia = ev(parametros={"manana": "11/10/2026"})
    assert (
        await run(FakeLlm([reply()]), plan=plan, accion=action, evidencia=evidencia)
    ).decision == ("permitir")


async def test_missing_search_is_correctable():
    llm = FakeLlm([])
    verdict = await run(llm, evidencia=ev(search=False))
    assert verdict.decision == "denegar_corregible" and "searched" in verdict.motivo


async def test_diff_only_counts_changed_fields():
    ok = reply(
        cambios=[
            {"campo": "nombre", "inicial": "Ana", "nuevo": "Ana"},
            {"campo": "telefono", "inicial": "1", "nuevo": "555-1"},
        ]
    )
    assert (await run(FakeLlm([ok]))).decision == "permitir"
    extra = reply(cambios=[{"campo": "email", "inicial": "a@x", "nuevo": "b@x"}])
    assert (await run(FakeLlm([extra]))).decision == "denegar_corregible"


async def test_two_candidates_is_final_and_names_the_distinguishing_data():
    two = reply(
        candidatos=[
            {"nombre": "Ana Pérez", "dato_distintivo": "documento 111"},
            {"nombre": "Ana Perez", "dato_distintivo": "documento 222"},
        ]
    )
    verdict = await run(FakeLlm([two]))
    assert verdict.decision == "denegar_final"
    assert any("111" in d for d in verdict.datos) and any("222" in d for d in verdict.datos)


async def test_create_of_existing_entity_is_final():
    plan = Plan(
        tipo="crear",
        entidad="Ana",
        escrituras_permitidas=[{"entidad": "Cliente", "accion": "create", "campos": {}}],
    )
    verdict = await run(
        FakeLlm([reply(escritura="create", ya_existe=True, datos=["CL-0141"])]), plan=plan
    )
    assert verdict.decision == "denegar_final" and verdict.datos == ["CL-0141"]


async def test_second_export_is_denied_final():
    state = GateState()
    llm = FakeLlm([reply(escritura="export")])
    first = await run(llm, plan=EXPORT_PLAN, state=state)
    second = await run(llm, plan=EXPORT_PLAN, state=state)
    assert first.decision == "permitir" and state.exports_authorized == 1
    assert second.decision == "denegar_final" and len(llm.calls) == 1


async def test_second_export_in_a_mixed_plan_is_caught_after_the_model():
    plan = Plan(
        tipo="editar",
        escrituras_permitidas=[
            {"entidad": "R", "accion": "export", "campos": {}},
            {"entidad": "C", "accion": "update", "campos": {}},
        ],
    )
    state = GateState(exports_authorized=1)
    verdict = await run(FakeLlm([reply(escritura="export")]), plan=plan, state=state)
    assert verdict.decision == "denegar_final"


async def test_auditor_error_is_correctable_then_final():
    state = GateState()
    llm = FakeLlm([LlmError("down"), "garbage", "garbage"])
    first = await run(llm, state=state)
    second = await run(llm, state=state)
    assert first.decision == "denegar_corregible"
    assert second.decision == "denegar_final"


async def test_auditor_denial_passes_through():
    verdict = await run(FakeLlm([reply(decision="denegar_corregible", motivo="wrong field")]))
    assert verdict.decision == "denegar_corregible" and verdict.motivo == "wrong field"


async def test_confirmation_dialog_of_an_authorized_export_is_a_continuation():
    state = GateState(exports_authorized=1)
    evidencia = ev(continuacion_de="export authorized: filters visible")
    llm = FakeLlm([reply(escritura="export", continuacion=True)])
    verdict = await run(llm, plan=EXPORT_PLAN, evidencia=evidencia, state=state)
    assert verdict.decision == "permitir" and state.exports_authorized == 1
    assert "PREVIOUS_WRITE: export authorized" in llm.calls[0]["messages"][-1]["content"]


async def test_continuation_claim_without_a_previous_write_does_not_bypass_once_only():
    state = GateState(exports_authorized=1)
    verdict = await run(
        FakeLlm([reply(escritura="export", continuacion=True)]),
        plan=Plan(
            tipo="exportar",
            escrituras_permitidas=[
                {"entidad": "R", "accion": "export", "campos": {}},
                {"entidad": "C", "accion": "update", "campos": {}},
            ],
        ),
        state=state,
    )
    assert verdict.decision == "denegar_final"


async def test_denial_for_a_value_tells_the_exact_expected_one():
    plan = Plan(
        tipo="crear",
        entidad="Papelería Los Andes",
        escrituras_permitidas=[
            {"entidad": "Cliente", "accion": "create", "campos": {"nombre": "Papelería Los Andes"}}
        ],
    )
    action = Action(
        tipo="fill_form",
        campos=[{"x": 1, "y": 1, "etiqueta": "Nombre", "valor": "Papeleria Los Andes"}],
    )
    verdict = await run(FakeLlm([]), plan=plan, accion=action)
    assert verdict.decision == "denegar_corregible"
    assert "use exactly: 'Papelería Los Andes'" in verdict.motivo


async def test_auditor_moderation_block_uses_the_fallback_model():
    llm = FakeLlm([ModerationBlocked("403"), reply()])
    verdict = await authorize(
        llm, UPDATE_PLAN, CLICK, ev(), model="aud", fallback="backup", state=GateState()
    )
    assert verdict.decision == "permitir"
    assert [c["model"] for c in llm.calls] == ["aud", "backup"]
