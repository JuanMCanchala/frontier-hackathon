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
