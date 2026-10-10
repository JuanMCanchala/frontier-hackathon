"""Schema of the local mini-app tasks. They stay out of eval/pruebas so a harness
run against the practice desktop does not pick them up. The mini app has no /__check."""

from pathlib import Path

from eval.schema import load_dir
from eval.scoring import categoria

DIR = Path(__file__).resolve().parents[1] / "eval" / "pruebas_mini"
PRACTICE_WORDS = ("cliente", "factura", "pedido")
NOTE = "Valvula encargada al almacen"


def _by_id():
    loaded = load_dir(DIR)
    assert loaded, f"no hay JSON en {DIR}"
    assert all(t.prueba is not None for t in loaded), [t.error for t in loaded if t.error]
    return {t.prueba.id: t.prueba for t in loaded}


def test_three_tasks_cover_create_cell_and_paraphrase() -> None:
    pruebas = _by_id()
    assert set(pruebas) == {"M1", "M2", "M3"}

    create = pruebas["M1"]
    assert categoria(create) == "crear"
    assert create.estado_esperado == "terminada"
    fields = {item.campo: item.valor for item in create.diff_esperado}
    assert fields == {"name": "Telefunken 540", "bench": "Mesa 4", "note": "Sin antena"}
    assert {item.id for item in create.diff_esperado} == {"a5"}
    assert {item.accion for item in create.diff_esperado} == {"create"}

    edit = pruebas["M2"]
    paraphrase = pruebas["M3"]
    assert categoria(edit) == "editar"
    assert categoria(paraphrase) == "editar"
    assert edit.diff_esperado == paraphrase.diff_esperado
    assert edit.instruccion != paraphrase.instruccion
    assert len(edit.diff_esperado) == 1
    change = edit.diff_esperado[0]
    assert change.accion == "update"
    assert change.id == "a4"
    assert change.campo == "note"
    assert change.valor == NOTE

    for prueba in pruebas.values():
        assert prueba.lista_blanca == []
        blob = prueba.instruccion.casefold()
        for word in PRACTICE_WORDS:
            assert word not in blob
