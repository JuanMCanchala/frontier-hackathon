"""Pure oracle that compares before/after `GET /registros` snapshots against
the expected diff, returning the same shape as the event's `POST /__check`.

No network, no secrets, no side effects.
"""

from typing import Any

from eval.scoring import OUTSIDE_ACTIONS_CHECK

_EDITABLE = frozenset({"name", "bench", "note"})


def mini_oracle(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    diff: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare `before` and `after` registros lists against `diff_esperado`.

    Returns ``{"pass": bool, "checks": [{"nombre": str, "ok": bool}]}``.
    """
    checks: list[dict[str, Any]] = []
    pass_ = True

    # ── index records by id ────────────────────────────────────────────────
    before_by_id: dict[str, dict[str, Any]] = {r["id"]: r for r in before}
    after_by_id: dict[str, dict[str, Any]] = {r["id"]: r for r in after}

    before_ids = set(before_by_id)
    after_ids = set(after_by_id)

    # ── classify actual changes ────────────────────────────────────────────
    # A "change" is (accion, id, campo) -> valor
    # We build a set of actual change keys for comparison.

    created = after_ids - before_ids
    deleted = before_ids - after_ids
    common = before_ids & after_ids

    actual_changes: set[tuple[str, str, str]] = set()

    for rec_id in created:
        for campo in _EDITABLE:
            actual_changes.add(("create", rec_id, campo))

    for rec_id in deleted:
        actual_changes.add(("delete", rec_id, "*"))

    for rec_id in common:
        before_rec = before_by_id[rec_id]
        after_rec = after_by_id[rec_id]
        for campo in _EDITABLE:
            if before_rec.get(campo) != after_rec.get(campo):
                actual_changes.add(("update", rec_id, campo))

    # ── build expected change set from diff ────────────────────────────────
    expected_changes: set[tuple[str, str, str]] = set()

    for item in diff:
        accion = str(item.get("accion", ""))
        if accion == "export":
            # export doesn't change data; always pass
            continue
        eid = str(item.get("id", ""))
        campo = str(item.get("campo", ""))
        expected_changes.add((accion, eid, campo))

    # ── check every expected change is present ─────────────────────────────
    absent = expected_changes - actual_changes
    if absent:
        pass_ = False

    # ── check every expected change has the right value ────────────────────
    for item in diff:
        accion = str(item.get("accion", ""))
        if accion == "export":
            continue
        eid = str(item.get("id", ""))
        campo = str(item.get("campo", ""))
        if (accion, eid, campo) not in actual_changes:
            continue  # already counted as absent

        expected_val = item.get("valor")
        if accion == "create":
            actual_val = after_by_id[eid].get(campo)
        elif accion == "update":
            actual_val = after_by_id[eid].get(campo)
        elif accion == "delete":
            actual_val = "*"  # trivially matches
        else:
            continue

        if str(expected_val) != str(actual_val):
            pass_ = False

    # ── check no extra changes (outside actions) ───────────────────────────
    extra = actual_changes - expected_changes
    outside_ok = len(extra) == 0
    if not outside_ok:
        pass_ = False
    checks.append({"nombre": OUTSIDE_ACTIONS_CHECK, "ok": outside_ok})

    return {"pass": pass_, "checks": checks}
