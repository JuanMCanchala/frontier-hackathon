"""Aggregation and console rendering of a harness run."""

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

CATEGORIES = ["crear", "editar", "preguntar", "exportar", "debe_fallar"]


@dataclass
class Resultado:
    id: str
    fuente: str
    categoria: str
    repeticion: int
    task_id: str | None = None
    estado: str | None = None
    resumen: str = ""
    sin_respuesta: bool = False
    post_latencia_s: float | None = None
    post_lento: bool = False
    duracion_s: float | None = None
    check: dict[str, Any] | None = None
    reglas: list[dict[str, Any]] = field(default_factory=list)
    exito: bool = False
    falso_exito: bool = False
    destructiva: bool = False
    costo_usd: float | None = None
    llamadas: list[dict[str, Any]] = field(default_factory=list)
    error_prueba: str | None = None
    motivos: list[str] = field(default_factory=list)


@dataclass
class RunReport:
    resultados: list[Resultado] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    presupuesto_cortado: float | None = None
    repeticiones: int = 1


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def _fmt(value: float | None, unit: str = "s") -> str:
    return "-" if value is None else f"{value:.1f}{unit}"


def _row(name: str, runs: list[Resultado], repeticiones: int) -> list[str]:
    n = len(runs)
    times = [r.duracion_s for r in runs if r.duracion_s is not None]
    costs = [r.costo_usd for r in runs if r.costo_usd is not None]
    cost = f"${sum(costs) / len(costs):.3f}" if costs else "sin dato"
    row = [
        name,
        str(n),
        f"{sum(r.exito for r in runs)}/{n}",
        str(sum(r.falso_exito for r in runs)),
        str(sum(r.destructiva for r in runs)),
        _fmt(percentile(times, 50)),
        _fmt(percentile(times, 95)),
        cost,
    ]
    if repeticiones > 1:
        by_test: dict[str, list[bool]] = defaultdict(list)
        for r in runs:
            by_test[f"{r.fuente}|{r.id}"].append(r.exito)
        passed = sum(1 for v in by_test.values() if len(v) == repeticiones and all(v))
        row.append(f"{passed}/{len(by_test)}")
    return row


def _table(rows: list[list[str]], header: list[str]) -> list[str]:
    widths = [max(len(r[i]) for r in [header, *rows]) for i in range(len(header))]
    fmt = lambda r: "  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)).rstrip()  # noqa: E731
    return [fmt(header), fmt(["-" * w for w in widths]), *(fmt(r) for r in rows)]


def failure_reason(r: Resultado) -> str:
    return "; ".join(r.motivos) if r.motivos else "sin motivo registrado"


def render(report: RunReport) -> str:
    valid = [r for r in report.resultados if r.error_prueba is None]
    errors = [r for r in report.resultados if r.error_prueba is not None]
    header = ["categoría", "n", "éxito", "falso éxito", "destructivas", "p50", "p95", "costo medio"]
    if report.repeticiones > 1:
        header.append(f"pass^{report.repeticiones}")
    rows = []
    for cat in CATEGORIES:
        runs = [r for r in valid if r.categoria == cat]
        if runs:
            rows.append(_row(cat, runs, report.repeticiones))
    rows.append(_row("TOTAL", valid, report.repeticiones))
    lines = _table(rows, header)
    lines.append("")
    for r in valid:
        if not r.exito or r.post_lento:
            lines.append(f"{r.id} #{r.repeticion} [{r.categoria}]: {failure_reason(r)}")
    for r in errors:
        lines.append(f"{r.id}: ERROR DE PRUEBA (no cuenta en la tabla): {r.error_prueba}")
    if report.presupuesto_cortado is not None:
        lines.append(
            f"CORRIDA CORTADA: gasto de la key {report.presupuesto_cortado:.2f} USD >= presupuesto"
        )
    lines.extend(f"AVISO: {a}" for a in report.avisos)
    return "\n".join(lines)
