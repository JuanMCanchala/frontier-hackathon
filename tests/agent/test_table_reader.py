import json

import pytest
from PIL import Image

from aprendiz.agent.schemas import Consulta
from aprendiz.agent.table_reader import (
    TableAccumulator,
    TableError,
    TablePage,
    aggregate,
    parse_number,
    read_table_page,
    read_table_page_checked,
    vote,
)
from tests.agent.fakes import FakeLlm

COLS = ["Factura", "Cliente", "Estado", "Total"]
CONSULTA = Consulta(
    operacion="conteo",
    entidad="Ferretería Ruiz",
    columna_entidad="Cliente",
    filtros={"Estado": "vencidas"},
)


def page(rows, siguiente=True, total=None) -> TablePage:
    return TablePage(
        columnas=COLS,
        filas=[dict(zip(COLS, r, strict=True)) for r in rows],
        pagina_actual=None,
        hay_siguiente=siguiente,
        total_visible=total,
    )


def rows(start, ruiz_vencidas, ruiz_pagadas=0, hijos=0):
    n = start
    out = []
    for client, state, count in (
        ("Ferretería Ruiz", "Vencida", ruiz_vencidas),
        ("Ferreteria  RUIZ", "Pagada", ruiz_pagadas),
        ("Ferretería Ruiz e Hijos", "Vencida", hijos),
    ):
        for _ in range(count):
            n += 1
            out.append((f"F-{n:03d}", client, state, "$ 100.000"))
    return out


def test_three_pages_with_lookalike_entity_and_mixed_states_count_exactly_45():
    pages = [
        page(rows(0, 12, 3, 5)),
        page(rows(100, 13, 2, 5)),
        page(rows(200, 20, 0, 0), siguiente=False, total=60),
    ]
    result = aggregate(CONSULTA, pages)
    assert result.valor == "45"
    assert result.desglose == "p1: 12, p2: 13, p3: 20 → 45"
    assert result.filas_leidas == 60 and result.coherente is True


def test_total_that_disagrees_with_the_rows_read_is_flagged():
    result = aggregate(CONSULTA, [page(rows(0, 12), siguiente=False, total=40)])
    assert result.coherente is False


def test_a_repeated_page_is_not_counted_twice():
    acc = TableAccumulator()
    assert acc.add(page(rows(0, 12)))
    assert acc.add(page(rows(100, 13)))
    assert not acc.add(page(rows(100, 13)))
    assert aggregate(CONSULTA, acc.pages).valor == "25"


def test_sum_with_thousands_separators_and_currency():
    consulta = Consulta(
        operacion="suma", entidad="Ferretería Ruiz", columna_entidad="Cliente", columna_suma="Total"
    )
    pages = [
        page(
            [
                ("F-1", "Ferretería Ruiz", "Vencida", "1.810.000"),
                ("F-2", "Ferretería Ruiz", "Pagada", "$ 250.000,50"),
                ("F-3", "Ferretería Ruiz e Hijos", "Vencida", "9.000.000"),
            ],
            siguiente=False,
        )
    ]
    assert aggregate(consulta, pages).valor == "2060000.5"


def test_sum_with_an_unreadable_amount_refuses_to_answer():
    consulta = Consulta(
        operacion="suma", entidad="X", columna_entidad="Cliente", columna_suma="Total"
    )
    bad = page([("F-1", "X", "Vencida", "ver detalle")], siguiente=False)
    with pytest.raises(TableError, match="sin valor numérico"):
        aggregate(consulta, [bad])


def test_entity_not_found_anywhere_refuses_instead_of_answering_zero():
    consulta = Consulta(operacion="conteo", entidad="Ferretería Ruiz")
    other = TablePage(
        columnas=["Factura", "Monto"],
        filas=[{"Factura": "F-1", "Monto": "5"}],
        pagina_actual=None,
        hay_siguiente=False,
        total_visible=None,
    )
    with pytest.raises(TableError):
        aggregate(consulta, [other])


def test_entity_column_present_with_no_matching_row_is_a_legit_zero():
    only_others = page([("F-1", "Otra SA", "Vencida", "1")], siguiente=False)
    assert aggregate(CONSULTA, [only_others]).valor == "0"


def test_sum_requires_a_column():
    with pytest.raises(ValueError):
        Consulta(operacion="suma")


@pytest.mark.parametrize(
    "text,expected", [("1.810.000", "1810000"), ("$ 45", "45"), ("12,5", "12.5"), ("n/a", None)]
)
def test_parse_number(text, expected):
    got = parse_number(text)
    assert (str(got) if got is not None else None) == expected


async def test_read_table_page_zips_rows_to_columns_and_never_filters():
    reply = json.dumps(
        {
            "columnas": ["A", "B"],
            "filas": [["1", "x"], ["2"], ["3", None]],
            "pagina_actual": 2,
            "hay_siguiente": True,
            "total_visible": 45,
        }
    )
    llm = FakeLlm([reply])
    got = await read_table_page(llm, Image.new("RGB", (8, 8)), model="m")
    assert got.filas == [{"A": "1", "B": "x"}, {"A": "2", "B": None}, {"A": "3", "B": None}]
    assert got.hay_siguiente and got.total_visible == 45
    assert "Do NOT count, filter" in llm.calls[0]["messages"][0]["content"]


def reply_json(rows_, siguiente=True) -> str:
    return json.dumps(
        {
            "columnas": COLS,
            "filas": [list(r) for r in rows_],
            "pagina_actual": 1,
            "hay_siguiente": siguiente,
            "siguiente": {"x": 200, "y": 684} if siguiente else None,
            "anterior": None,
            "total_visible": None,
        }
    )


def test_vote_restores_a_row_one_read_skipped_and_drops_one_it_invented():
    full = rows(0, 5)
    skipped = page(full[:2] + full[3:])
    invented = page([*full, ("F-999", "Ferretería Ruiz", "Vencida", "$ 1")])
    merged = vote([skipped, page(full), invented])
    assert [r["Factura"] for r in merged.filas] == [r[0] for r in full]


async def test_agreeing_reads_need_no_third_call():
    llm = FakeLlm([reply_json(rows(0, 4))] * 2)
    got = await read_table_page_checked(llm, Image.new("RGB", (8, 8)), model="m")
    assert len(llm.calls) == 2 and len(got.filas) == 4
    assert got.siguiente is not None and (got.siguiente.x, got.siguiente.y) == (200, 684)


async def test_disagreeing_reads_are_settled_by_a_third():
    full = rows(0, 6)
    llm = FakeLlm([reply_json(full[:5]), reply_json(full), reply_json(full)])
    got = await read_table_page_checked(llm, Image.new("RGB", (8, 8)), model="m")
    assert len(llm.calls) == 3
    assert aggregate(CONSULTA, [got]).valor == "6"


def test_a_page_read_again_with_one_garbled_cell_is_still_a_repeat():
    acc = TableAccumulator()
    first = rows(0, 10)
    garbled = [("F-00l", *first[0][1:]), *first[1:]]
    assert acc.add(page(first))
    assert not acc.add(page(garbled))
    assert acc.add(page(rows(100, 10)))
