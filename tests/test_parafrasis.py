"""Paraphrased instructions stay equivalent to eval/pruebas without copying hidden answers."""

import re
from pathlib import Path
from typing import Any

from eval.schema import LoadedTest, Prueba, load_dir

ROOT = Path(__file__).resolve().parents[1]
ORIGINALS = ROOT / "eval" / "pruebas"
PARAPHRASES = ROOT / "eval" / "parafrasis"

# P1–P12. eval/pruebas stays exactly V1–V12.
REQUIRED_NUMBERS = set(range(1, 13))

# A calendar date or a code stays one token. Splitting 2026-10-15 would hide a leak
# behind a year that another instruction already contains.
_TOKEN = re.compile(r"\d{4}-\d{2}-\d{2}|[0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+(?:-[0-9A-Za-z]+)*")

_ACTION_VERBS = {
    "eliminar": ("elimina", "eliminar", "borra", "borrar"),
    "exportar": ("exporta", "exportar", "exporte"),
    "registrar": ("registra", "registrar", "alta"),
    "editar": (
        "cambia",
        "cambiar",
        "actualiza",
        "actualizar",
        "modifica",
        "modificar",
        "sustituye",
        "sustituir",
    ),
}

_UNCHANGED = (
    "nivel",
    "estado_esperado",
    "diff_esperado",
    "lista_blanca",
    "respuesta_esperada",
    "claves_razon",
)


def _squash(text: str) -> str:
    return " ".join(text.casefold().split())


def _tokens(text: str) -> set[str]:
    return {match.group(0).casefold() for match in _TOKEN.finditer(text)}


def _leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        found: list[str] = []
        for item in value.values():
            found.extend(_leaves(item))
        return found
    if isinstance(value, list):
        found = []
        for item in value:
            found.extend(_leaves(item))
        return found
    return []


def _forbidden_tokens(prueba: Prueba, original: str) -> set[str]:
    chunks: list[str] = []
    if prueba.respuesta_esperada:
        chunks.append(prueba.respuesta_esperada)
    chunks.extend(prueba.claves_razon or [])
    for item in prueba.diff_esperado:
        chunks.append(item.id)
        chunks.append(item.campo)
        chunks.extend(_leaves(item.valor))
    known = _tokens(original)
    banned: set[str] = set()
    for chunk in chunks:
        banned |= _tokens(chunk) - known
    return banned


def _verbs(text: str) -> set[str]:
    folded = text.casefold()
    return {
        name
        for name, verbs in _ACTION_VERBS.items()
        if any(re.search(rf"\b{verb}\b", folded) for verb in verbs)
    }


def _suffix(source: str, test_id: str) -> str:
    stem = Path(source).name.removesuffix(".json")
    prefix = f"{test_id}-"
    assert stem.startswith(prefix), stem
    return stem[len(prefix) :]


def _index(directory: Path, prefix: str) -> dict[int, LoadedTest]:
    found: dict[int, LoadedTest] = {}
    for item in load_dir(directory):
        assert item.prueba is not None, item.error
        match = re.fullmatch(rf"{prefix}(\d+)", item.prueba.id)
        assert match is not None, item.prueba.id
        number = int(match.group(1))
        assert number not in found, item.prueba.id
        found[number] = item
    return found


def _assert_pair(original: LoadedTest, paraphrase: LoadedTest) -> None:
    assert original.prueba is not None
    assert paraphrase.prueba is not None
    source = original.prueba
    rewritten = paraphrase.prueba
    assert _suffix(original.source, source.id) == _suffix(paraphrase.source, rewritten.id)
    assert source.model_dump(include=set(_UNCHANGED)) == rewritten.model_dump(
        include=set(_UNCHANGED)
    )
    old = source.instruccion
    new = rewritten.instruccion
    assert _squash(new) != _squash(old)
    assert re.search(r"[áéíóúñü¿¡]", new, re.IGNORECASE) or re.search(
        r"\b(el|la|los|las|de|del|que)\b", new, re.IGNORECASE
    )
    for number in re.findall(r"\d{3,}", old):
        assert number in new
    for code in re.findall(r"\b[A-Za-z]{2}-\d{4}\b", old):
        assert code in new
    folded_old = old.casefold()
    folded_new = new.casefold()
    if "ayer" in folded_old:
        assert "ayer" in folded_new or "el día anterior a hoy" in folded_new
    if "mañana" in folded_old:
        assert "mañana" in folded_new or "el día siguiente a hoy" in folded_new
    for date in re.findall(r"\d{4}-\d{2}-\d{2}", new):
        assert date in old
    leaked = _tokens(new) & _forbidden_tokens(source, old)
    assert not leaked, leaked
    if "¿" in old:
        assert "¿" in new
        assert not _verbs(new)
    else:
        assert "¿" not in new
        assert _verbs(old) == _verbs(new)


def test_paraphrase_ids_are_exactly_the_required_set() -> None:
    paraphrases = _index(PARAPHRASES, "P")
    assert set(paraphrases) == REQUIRED_NUMBERS


def test_each_existing_pair_keeps_the_wording_invariant() -> None:
    originals = _index(ORIGINALS, "V")
    paraphrases = _index(PARAPHRASES, "P")
    assert set(paraphrases) == REQUIRED_NUMBERS
    for number in sorted(REQUIRED_NUMBERS):
        _assert_pair(originals[number], paraphrases[number])


def test_original_directory_is_exactly_v1_through_v12() -> None:
    originals = _index(ORIGINALS, "V")
    assert set(originals) == set(range(1, 13))
