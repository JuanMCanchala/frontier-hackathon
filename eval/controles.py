"""Recall of writes and the rate of reads marked as writes.

Pure function over rows that a person already labeled. It does not read traces.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

# The gate only sees these two. Navigation and search skip it.
_SEEN_AS_WRITE = frozenset({"compromiso", "campo_registro"})
_MISSED_AS_READ = frozenset({"navegacion", "busqueda_filtro"})


@dataclass(frozen=True)
class ControlMeasures:
    """Counts for the three questions in section 8.3. Rates stay None when the denominator is 0."""

    rows: int
    writes: int
    writes_seen: int
    reads: int
    reads_marked_as_write: int
    detector_persisted: int
    detector_eligible: int

    @property
    def write_recall(self) -> float | None:
        if self.writes == 0:
            return None
        return self.writes_seen / self.writes

    @property
    def read_marked_rate(self) -> float | None:
        if self.reads == 0:
            return None
        return self.reads_marked_as_write / self.reads


def _counts(row: Mapping[str, object]) -> bool:
    """Execution steps the classifier actually answered. Verification is read-only mode."""
    if row.get("fase") != "ejecucion":
        return False
    if row.get("clasificacion") is None:
        return False
    return row.get("etiqueta") in ("lectura", "escritura")


def measure_controls(rows: Sequence[Mapping[str, object]]) -> ControlMeasures:
    """Recall uses execution only. A denial is not a miss and a permit is not a hit.

    The detector question is only the writes the classifier let through
    (navegacion or busqueda_filtro). Zero of those is 0 of 0, not 100 %.
    """
    seen = [row for row in rows if _counts(row)]
    writes = [row for row in seen if row["etiqueta"] == "escritura"]
    reads = [row for row in seen if row["etiqueta"] == "lectura"]
    missed = [row for row in writes if row["clasificacion"] in _MISSED_AS_READ]
    return ControlMeasures(
        rows=len(seen),
        writes=len(writes),
        writes_seen=sum(row["clasificacion"] in _SEEN_AS_WRITE for row in writes),
        reads=len(reads),
        reads_marked_as_write=sum(row["clasificacion"] in _SEEN_AS_WRITE for row in reads),
        detector_persisted=sum(row.get("persistio") is True for row in missed),
        detector_eligible=len(missed),
    )
