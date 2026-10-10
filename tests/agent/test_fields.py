import pytest

from aprendiz.agent.fields import value_matches


@pytest.mark.parametrize(
    "read,expected",
    [
        ("Papeleria Los Andes", "Papelería Los Andes"),
        ("PAPELERÍA  los andes", "Papelería Los Andes"),
        ("  Ramirez ", "Ramírez"),
        ("Nariño", "Narino"),
        ("Ana", "Ana"),
    ],
)
def test_accent_case_and_spacing_differences_match(read, expected):
    assert value_matches(read, expected)


@pytest.mark.parametrize(
    "read,expected",
    [
        ("900123457", "900123456"),
        ("Papeleria Los Andez", "Papelería Los Andes"),
        ("Papelería Los", "Papelería Los Andes"),
        ("Ana", "Anna"),
        ("", "Ana"),
        (None, "Ana"),
    ],
)
def test_different_letters_digits_or_length_do_not_match(read, expected):
    assert not value_matches(read, expected)
