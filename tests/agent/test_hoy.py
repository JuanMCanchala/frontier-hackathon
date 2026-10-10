import json
from datetime import date

import pytest
from PIL import Image

from aprendiz.agent.hoy import ParamError, parse_date_text, read_today, resolve_params
from aprendiz.agent.llm import LlmError
from tests.agent.fakes import FakeLlm

SAT = date(2026, 10, 10)  # a Saturday


def test_fixture_day_is_saturday():
    assert SAT.weekday() == 5


@pytest.mark.parametrize(
    "text,expected",
    [
        ("10/10/2026", SAT),
        ("Hoy: 10-10-2026", SAT),
        ("2026-10-10", SAT),
        ("10.10.2026", SAT),
        ("sábado, 10 de octubre de 2026", SAT),
        ("10 oct 2026", SAT),
        ("5/3/2026", date(2026, 3, 5)),
        ("31/02/2026", None),
        ("sin fecha", None),
    ],
)
def test_parse_date_text(text, expected):
    assert parse_date_text(text) == expected


def test_day_offsets():
    out = resolve_params(
        {"a": "hoy_app+1", "b": "hoy_app-1", "c": "hoy_app", "d": "hoy_app+7"}, SAT
    )
    assert out == {
        "a": date(2026, 10, 11),
        "b": date(2026, 10, 9),
        "c": SAT,
        "d": date(2026, 10, 17),
    }


def test_offsets_cross_month_year_and_leap_day():
    assert resolve_params({"a": "hoy_app+1"}, date(2026, 12, 31))["a"] == date(2027, 1, 1)
    assert resolve_params({"a": "hoy_app+1"}, date(2028, 2, 28))["a"] == date(2028, 2, 29)
    assert resolve_params({"a": "hoy_app-1"}, date(2026, 3, 1))["a"] == date(2026, 2, 28)


def test_weekdays_are_strictly_after_or_before_today():
    out = resolve_params(
        {
            "next_mon": "proximo_lunes",
            "prev_mon": "anterior_lunes",
            "next_sat": "proximo_sabado",
            "prev_sat": "anterior_sabado",
            "next_wed": "proximo_miércoles",
        },
        SAT,
    )
    assert out["next_mon"] == date(2026, 10, 12)
    assert out["prev_mon"] == date(2026, 10, 5)
    assert out["next_sat"] == date(2026, 10, 17)  # today is a Saturday: a week ahead
    assert out["prev_sat"] == date(2026, 10, 3)
    assert out["next_wed"] == date(2026, 10, 14)


@pytest.mark.parametrize("expr", ["hoy_app*2", "proximo_festivo", "2026-10-10"])
def test_unsupported_expression_raises(expr):
    with pytest.raises(ParamError):
        resolve_params({"x": expr}, SAT)


IMG = Image.new("RGB", (8, 8))


async def test_read_today_parses_the_text_in_code():
    llm = FakeLlm([json.dumps({"texto": "10/10/2026"})])
    assert await read_today(llm, IMG, model="m") == SAT


async def test_read_today_none_when_not_visible_or_invalid_or_error():
    assert await read_today(FakeLlm(['{"texto": null}']), IMG, model="m") is None
    assert await read_today(FakeLlm(['{"texto": "31/02/2026"}']), IMG, model="m") is None
    assert await read_today(FakeLlm([LlmError("x")]), IMG, model="m") is None


def test_period_and_absolute_expressions_need_no_today():
    out = resolve_params(
        {
            "desde": "inicio_mes:2026-09",
            "hasta": "fin_mes:2026-09",
            "feb": "fin_mes:2028-02",
            "ini": "inicio_anio:2026",
            "fin": "fin_anio:2026",
            "dia": "fecha:2026-09-15",
        },
        None,
    )
    assert out == {
        "desde": date(2026, 9, 1),
        "hasta": date(2026, 9, 30),
        "feb": date(2028, 2, 29),
        "ini": date(2026, 1, 1),
        "fin": date(2026, 12, 31),
        "dia": date(2026, 9, 15),
    }


def test_needs_today_only_for_relative_expressions():
    from aprendiz.agent.hoy import needs_today

    assert not needs_today({"a": "inicio_mes:2026-09", "b": "fecha:2026-09-15"})
    assert needs_today({"a": "inicio_mes:2026-09", "b": "hoy_app+1"})


@pytest.mark.parametrize(
    "expr",
    [
        "inicio_mes:2026-13",
        "fecha:2026-02-30",
        "fin_mes:2026",
        "inicio_anio:2026-01",
        "fecha:2026-09",
    ],
)
def test_invalid_period_expressions_raise(expr):
    with pytest.raises(ParamError):
        resolve_params({"x": expr}, SAT)


def test_relative_expression_without_today_raises():
    with pytest.raises(ParamError, match="needs the app's date"):
        resolve_params({"x": "hoy_app+1"}, None)


def test_named_relative_dates_from_a_given_today():
    hoy = date(2026, 10, 14)  # a Wednesday
    out = resolve_params(
        {
            "a": "hoy", "b": "ayer", "c": "anteayer", "d": "manana",
            "e": "semana_actual_inicio", "f": "semana_actual_fin",
            "g": "semana_pasada_inicio", "h": "semana_pasada_fin",
            "i": "mes_actual_inicio", "j": "mes_actual_fin",
            "k": "mes_pasado_inicio", "l": "mes_pasado_fin",
            "m": "anio_actual_inicio", "n": "anio_actual_fin",
            "o": "ultimos_dias:7", "p": "lunes", "q": "proximo_lunes",
        },
        hoy,
    )  # fmt: skip
    assert out == {
        "a": hoy, "b": date(2026, 10, 13), "c": date(2026, 10, 12), "d": date(2026, 10, 15),
        "e": date(2026, 10, 12), "f": date(2026, 10, 18),
        "g": date(2026, 10, 5), "h": date(2026, 10, 11),
        "i": date(2026, 10, 1), "j": date(2026, 10, 31),
        "k": date(2026, 9, 1), "l": date(2026, 9, 30),
        "m": date(2026, 1, 1), "n": date(2026, 12, 31),
        "o": date(2026, 10, 8), "p": date(2026, 10, 12), "q": date(2026, 10, 19),
    }  # fmt: skip


def test_last_month_across_a_year_boundary_and_a_monday_today():
    jan = resolve_params({"a": "mes_pasado_inicio", "b": "mes_pasado_fin"}, date(2027, 1, 15))
    assert jan == {"a": date(2026, 12, 1), "b": date(2026, 12, 31)}
    monday = date(2026, 10, 12)
    out = resolve_params(
        {"a": "semana_pasada_inicio", "b": "semana_pasada_fin", "c": "lunes"}, monday
    )
    assert out == {"a": date(2026, 10, 5), "b": date(2026, 10, 11), "c": date(2026, 10, 5)}
