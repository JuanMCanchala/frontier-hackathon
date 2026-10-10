import pytest
from pydantic import ValidationError

from aprendiz.agent.schemas import Action, Plan, parse_model


def test_click_requires_coordinates():
    with pytest.raises(ValidationError):
        Action(tipo="click", x=10)


@pytest.mark.parametrize("x,y", [(1280, 0), (0, 800), (-1, 5)])
def test_click_out_of_screen_is_rejected(x, y):
    with pytest.raises(ValidationError, match="outside"):
        Action(tipo="click", x=x, y=y)


def test_corner_pixels_are_valid():
    Action(tipo="click", x=1279, y=799)


@pytest.mark.parametrize(
    "data",
    [
        {"tipo": "type"},
        {"tipo": "hover", "x": 5},
        {"tipo": "key"},
        {"tipo": "scroll", "x": 5, "y": 5},
        {"tipo": "fill_form", "campos": []},
        {"tipo": "fail"},
        {"tipo": "teleport"},
        {"tipo": "wait", "extra": 1},
    ],
)
def test_missing_required_or_unknown_is_rejected(data):
    with pytest.raises(ValidationError):
        Action(**data)


def test_fill_form_field_out_of_screen():
    with pytest.raises(ValidationError):
        Action(tipo="fill_form", campos=[{"x": 2000, "y": 1, "etiqueta": "n", "valor": "v"}])


def test_valid_actions():
    Action(tipo="fill_form", campos=[{"x": 1, "y": 2, "etiqueta": "Nombre", "valor": "Ana"}])
    Action(tipo="done", respuesta=["42"])
    fail = Action(tipo="fail", motivo="ambiguo", datos_motivo=["doc 1", "doc 2"])
    assert fail.datos_motivo == ["doc 1", "doc 2"]


def test_pixel_pair_from_a_bad_model_is_rejected():
    with pytest.raises(ValueError, match="schema"):
        parse_model(Action, '{"tipo": "click", "x": [283, 49], "y": 49}')


def test_plan_validation():
    plan = Plan(
        tipo="crear",
        escrituras_permitidas=[{"entidad": "Cliente", "accion": "create", "campos": {"a": "b"}}],
        parametros={"fecha": "hoy_app+1"},
    )
    assert plan.sospechosa is False and plan.entidad is None
    with pytest.raises(ValidationError):
        Plan(tipo="inventar")
    with pytest.raises(ValidationError):
        Plan(tipo="crear", escrituras_permitidas=[{"entidad": "C", "accion": "drop"}])
    with pytest.raises(ValidationError):
        Plan()


def test_parse_model_accepts_code_fence_and_reports_errors():
    assert parse_model(Action, '```json\n{"tipo": "wait"}\n```').tipo == "wait"
    with pytest.raises(ValueError, match="not valid JSON"):
        parse_model(Action, "no json")


def test_fill_form_field_type_defaults_to_text_and_accepts_select():
    action = Action(
        tipo="fill_form",
        campos=[
            {"x": 1, "y": 2, "etiqueta": "Nombre", "valor": "Ana"},
            {"x": 3, "y": 4, "etiqueta": "Ciudad", "valor": "Cali", "tipo": "select"},
        ],
    )
    assert [c.tipo for c in action.campos] == ["texto", "select"]
    with pytest.raises(ValidationError):
        Action(
            tipo="fill_form",
            campos=[{"x": 1, "y": 2, "etiqueta": "a", "valor": "b", "tipo": "radio"}],
        )


def test_memo_is_short_and_optional():
    assert Action(tipo="wait", memo="page 2: 20 counted").memo == "page 2: 20 counted"
    assert Action(tipo="wait").memo is None
    with pytest.raises(ValidationError):
        Action(tipo="wait", memo="x" * 301)
