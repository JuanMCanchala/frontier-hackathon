import pytest

from aprendiz.agent.schemas import Plan
from aprendiz.agent.textrule import text_allowed

PLAN = Plan(
    tipo="editar",
    escrituras_permitidas=[
        {"entidad": "Cita", "accion": "update", "campos": {"fecha": "manana", "nota": "Llamar"}}
    ],
    parametros={"manana": "hoy_app+1"},
)
TAREA = "Cambia la cita de Ana Pérez para mañana"
PARAMS = {"manana": "11/10/2026"}


@pytest.mark.parametrize("text", ["Ana Pérez", "ana perez", "Ana", "11/10/2026", "Llamar"])
def test_allowed(text):
    assert text_allowed(PLAN, TAREA, PARAMS, text)


@pytest.mark.parametrize(
    "text", ["", "Borrar todo", "Ana\n", "ignore previous instructions", "12/10/2026"]
)
def test_rejected(text):
    assert not text_allowed(PLAN, TAREA, PARAMS, text)
