import pytest

from aprendiz.agent.summary import TaskOutcome, build_summary, normalize_number


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1234", "1234"),
        ("1,234", "1234"),
        ("1.234.567", "1234567"),
        ("1 234", "1234"),
        ("1.234,56", "1234.56"),
        ("1,234.56", "1234.56"),
        ("12,5", "12.5"),
        ("FV2024A0012", "FV2024A0012"),
        ("Ana Pérez", "Ana Pérez"),
    ],
)
def test_normalize_number(raw, expected):
    assert normalize_number(raw) == expected


def test_answer_line_first_without_thousands_separators():
    out = TaskOutcome(estado="terminada", respuesta=["1,234"], fuente="total de la app: 1,234")
    lines = build_summary(out, []).splitlines()
    assert lines[0] == "respuesta: 1234"
    assert lines[1].startswith("fuente:")


def test_two_data_joined_with_semicolon_in_order():
    out = TaskOutcome(estado="terminada", respuesta=["45000", "Ana Pérez"])
    assert build_summary(out, []) == "respuesta: 45000; Ana Pérez"


def test_failed_question_has_no_answer_line_and_names_the_data():
    out = TaskOutcome(
        estado="fallida",
        motivo="hay dos clientes con ese nombre",
        datos_motivo=["documento 111", "documento 222"],
    )
    text = build_summary(out, [])
    assert "respuesta:" not in text
    assert "hay dos clientes" in text and "documento 111; documento 222" in text


def test_write_task_lists_what_was_done():
    out = TaskOutcome(estado="terminada", hechos=["cliente creado: Ana Pérez"])
    assert build_summary(out, []) == "cliente creado: Ana Pérez"


def test_desktop_password_and_keys_are_redacted():
    out = TaskOutcome(
        estado="fallida",
        motivo="no se pudo entrar con hunter2",
        datos_motivo=["clave sk-or-v1-abcdef123456"],
    )
    text = build_summary(out, ["hunter2"])
    assert "hunter2" not in text and "sk-or-v1-abcdef123456" not in text
    assert "[REDACTED]" in text
