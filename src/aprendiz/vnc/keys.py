"""Keysym mapping for the X11 keysyms used by RFB KeyEvent messages."""

_NAMED_KEYS: dict[str, int] = {
    "enter": 0xFF0D,
    "return": 0xFF0D,
    "tab": 0xFF09,
    "escape": 0xFF1B,
    "esc": 0xFF1B,
    "backspace": 0xFF08,
    "delete": 0xFFFF,
    "del": 0xFFFF,
    "insert": 0xFF63,
    "home": 0xFF50,
    "left": 0xFF51,
    "up": 0xFF52,
    "right": 0xFF53,
    "down": 0xFF54,
    "pageup": 0xFF55,
    "pagedown": 0xFF56,
    "end": 0xFF57,
    "space": 0x20,
    "shift": 0xFFE1,
    "ctrl": 0xFFE3,
    "control": 0xFFE3,
    "alt": 0xFFE9,
    "meta": 0xFFE7,
    "super": 0xFFEB,
    "win": 0xFFEB,
    "cmd": 0xFFEB,
    **{f"f{n}": 0xFFBD + n for n in range(1, 13)},
}

_MODIFIERS = {"shift", "ctrl", "control", "alt", "meta", "super", "win", "cmd"}

KEYSYM_RETURN = 0xFF0D
KEYSYM_TAB = 0xFF09


def keysym_for_char(char: str) -> int:
    """Latin-1 printable characters map to themselves; the rest use the Unicode range."""
    if len(char) != 1:
        raise ValueError(f"expected a single character, got {char!r}")
    code = ord(char)
    if 0x20 <= code <= 0xFF:
        return code
    return 0x01000000 + code


def parse_combo(combo: str) -> list[int]:
    """Turn 'ctrl+shift+a' into keysyms in press order (modifiers first, key last)."""
    if combo.endswith("+"):  # the key itself is '+', e.g. 'ctrl++'
        parts = [p.strip() for p in combo[:-1].split("+") if p.strip()] + ["+"]
    else:
        parts = [p.strip() for p in combo.split("+")]
    if any(not p for p in parts):
        raise ValueError(f"invalid key combo: {combo!r}")
    keysyms = [_keysym_for_part(p) for p in parts]
    modifiers = [p for p in parts[:-1] if p.lower() in _MODIFIERS]
    if len(modifiers) != len(parts) - 1:
        raise ValueError(f"only the last element of a combo can be a non-modifier: {combo!r}")
    return keysyms


def _keysym_for_part(part: str) -> int:
    named = _NAMED_KEYS.get(part.lower())
    if named is not None:
        return named
    if len(part) == 1:
        return keysym_for_char(part)
    raise ValueError(f"unknown key name: {part!r}")
