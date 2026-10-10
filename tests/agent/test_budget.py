import json
from pathlib import Path

import pytest

from aprendiz.agent.budget import Budget, BudgetExceeded, GlobalSpend, SpendFileError


def test_reserve_blocks_when_worst_case_does_not_fit():
    budget = Budget(0.10)
    budget.reserve(0.06)
    with pytest.raises(BudgetExceeded):
        budget.reserve(0.05)
    assert budget.reserved == pytest.approx(0.06)


def test_settle_adjusts_to_real_cost_and_frees_the_difference():
    budget = Budget(0.10)
    res = budget.reserve(0.06)
    budget.settle(res, 0.01)
    assert budget.spent == pytest.approx(0.01)
    assert budget.reserved == 0
    budget.reserve(0.09)  # fits again only because the reservation was released


def test_release_returns_reservation_without_spending():
    budget = Budget(0.10)
    res = budget.reserve(0.08)
    budget.release(res)
    assert budget.spent == 0 and budget.remaining == pytest.approx(0.10)


def test_global_counter_persists_and_cuts(tmp_path: Path):
    path = tmp_path / "data" / "spend.json"
    first = Budget(1.0, GlobalSpend(path, 0.05))
    res = first.reserve(0.04)
    first.settle(res, 0.03)
    assert json.loads(path.read_text())["spent_usd"] == pytest.approx(0.03)

    second = Budget(1.0, GlobalSpend(path, 0.05))  # new process, same file
    with pytest.raises(BudgetExceeded, match="global"):
        second.reserve(0.03)
    assert second.reserved == 0  # task side not left reserved by a global refusal


def test_global_write_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "spend.json"
    spend = GlobalSpend(path, 10)
    spend.settle(0, 1.0)
    before = path.read_text()

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr("aprendiz.agent.budget.os.replace", boom)
    with pytest.raises(OSError):
        spend.settle(0, 2.0)
    assert path.read_text() == before
    assert [p.name for p in tmp_path.iterdir()] == ["spend.json"]  # no temp leftovers


def test_corrupt_spend_file_fails_loudly(tmp_path: Path):
    path = tmp_path / "spend.json"
    path.write_text("not json")
    with pytest.raises(SpendFileError):
        GlobalSpend(path, 1)
