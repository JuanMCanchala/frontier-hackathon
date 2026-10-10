import pytest

from aprendiz.vnc.keys import keysym_for_char, parse_combo


def test_keysym_mapping() -> None:
    assert keysym_for_char("a") == 0x61
    assert keysym_for_char("A") == 0x41
    assert keysym_for_char(" ") == 0x20
    assert keysym_for_char("á") == 0xE1
    assert keysym_for_char("ñ") == 0xF1
    assert keysym_for_char("¿") == 0xBF
    assert keysym_for_char("¡") == 0xA1
    assert keysym_for_char("ÿ") == 0xFF
    assert keysym_for_char("€") == 0x010020AC
    assert keysym_for_char("Ā") == 0x01000100


def test_combo_parsing() -> None:
    assert parse_combo("enter") == [0xFF0D]
    assert parse_combo("shift+tab") == [0xFFE1, 0xFF09]
    assert parse_combo("ctrl+shift+A") == [0xFFE3, 0xFFE1, 0x41]
    assert parse_combo("ctrl++") == [0xFFE3, 0x2B]
    with pytest.raises(ValueError):
        parse_combo("a+b")
    with pytest.raises(ValueError):
        parse_combo("hyperkey")
