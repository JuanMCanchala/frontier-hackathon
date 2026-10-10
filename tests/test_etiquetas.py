"""Recall and the read-marked-as-write rate over fixture rows. No traces."""

from eval.controles import measure_controls


def _row(
    etiqueta: str,
    clasificacion: str | None = "navegacion",
    *,
    fase: str = "ejecucion",
    persistio: bool | None = False,
) -> dict[str, object]:
    return {
        "fase": fase,
        "clasificacion": clasificacion,
        "etiqueta": etiqueta,
        "persistio": persistio,
    }


def test_recall_is_one_when_the_only_write_is_marked() -> None:
    measures = measure_controls([_row("escritura", "campo_registro", persistio=True)])
    assert measures.writes == 1
    assert measures.writes_seen == 1
    assert measures.write_recall == 1.0
    assert (measures.detector_persisted, measures.detector_eligible) == (0, 0)


def test_write_left_as_navigation_is_a_recall_miss() -> None:
    measures = measure_controls([_row("escritura", "navegacion", persistio=True)])
    assert measures.writes == 1
    assert measures.writes_seen == 0
    assert measures.write_recall == 0.0
    assert (measures.detector_persisted, measures.detector_eligible) == (1, 1)


def test_rate_of_reads_marked_as_write() -> None:
    measures = measure_controls(
        [
            _row("lectura", "compromiso"),
            _row("lectura", "navegacion"),
            _row("escritura", "campo_registro"),
        ]
    )
    assert measures.reads == 2
    assert measures.reads_marked_as_write == 1
    assert measures.read_marked_rate == 0.5


def test_detector_is_zero_of_zero_when_the_classifier_missed_nothing() -> None:
    measures = measure_controls(
        [
            _row("escritura", "compromiso", persistio=True),
            _row("escritura", "campo_registro", persistio=False),
            _row("lectura", "busqueda_filtro"),
        ]
    )
    assert measures.write_recall == 1.0
    assert (measures.detector_persisted, measures.detector_eligible) == (0, 0)


def test_verification_and_unclassified_steps_stay_out_of_recall() -> None:
    measures = measure_controls(
        [
            _row("escritura", "campo_registro"),
            _row("escritura", "compromiso", fase="verificacion"),
            _row("escritura", None),
        ]
    )
    assert measures.rows == 1
    assert measures.write_recall == 1.0
