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


@pytest.mark.parametrize("expr", ["mañana", "hoy_app*2", "proximo_festivo", "2026-10-10"])
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
