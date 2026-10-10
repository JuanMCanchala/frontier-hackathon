"""Text comparison helpers shared by the gate and the text rule."""

import difflib
import unicodedata
from collections.abc import Iterable


def fold(text: str) -> str:
    """Lowercase, accent-free (NFKD without diacritics), single-spaced: for comparing text."""
    stripped = unicodedata.normalize("NFKD", text.casefold())
    return " ".join("".join(c for c in stripped if unicodedata.category(c) != "Mn").split())


def exact_hint(value: str, candidates: Iterable[str]) -> str:
    """Names the allowed value `value` most resembles, so the actor can copy it verbatim."""
    originals = {fold(c): c for c in candidates}
    close = difflib.get_close_matches(fold(value), list(originals), n=1, cutoff=0.6)
    if not close:
        return ""
    return f"; use exactly: {originals[close[0]]!r}"


def fragment_of(tarea: str, value: str) -> bool:
    """Whether `value` is a literal fragment of the task (case, accents and spacing ignored).
    The task text is the only trusted input; line breaks are never allowed because typing
    one presses Enter."""
    if any(c in value for c in "\r\n\t"):
        return False
    folded = fold(value)
    return bool(folded) and folded in fold(tarea)


def _stem(word: str) -> str:
    """Crude Spanish stem: drops plural and gender endings (entregados/entregada -> entregad)."""
    if len(word) > 3 and word.endswith("s"):
        word = word[:-1]
    if len(word) > 4 and word.endswith("e"):
        word = word[:-1]
    if len(word) > 3 and word[-1] in "oa":
        word = word[:-1]
    return word


def stems(text: str) -> list[str]:
    return [_stem(w) for w in fold(text).split()]


def root_fragment_of(tarea: str, value: str) -> bool:
    """Like fragment_of, comparing words by stem, so singular/plural and gender do not matter.
    Only for fields whose value is bounded by the UI (selects): never for free text."""
    wanted, haystack = stems(value), stems(tarea)
    if not wanted:
        return False
    return any(
        haystack[i : i + len(wanted)] == wanted for i in range(len(haystack) - len(wanted) + 1)
    )
