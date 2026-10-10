"""App memory store: per-app routes and procedures, no business data."""

import sqlite3
from pathlib import Path

import pytest

from aprendiz.agent.memory import (
    AppIdentity,
    AppMemory,
    MemoryHints,
    MemoryRejected,
    render_hints,
)
from aprendiz.config import Settings

HASH_A = "a" * 16
HASH_B = "b" * 16


def identity(title: str = "Facturas 1.2", screen_hash: str = HASH_A) -> AppIdentity:
    return AppIdentity(title=title, screen_hash=screen_hash)


def test_settings_default_memory_path_is_separate_from_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("APP_MEMORY_DB", raising=False)
    settings = Settings(OPENROUTER_API_KEY="k", TEAM_TOKEN="t", _env_file=None)  # type: ignore[call-arg]
    assert settings.APP_MEMORY_DB == "data/app_memory.sqlite3"
    assert settings.APP_MEMORY_DB != settings.TASKS_DB


def test_save_and_list_only_the_matching_identity(tmp_path: Path) -> None:
    memory = AppMemory(tmp_path / "app_memory.sqlite3")
    route_id = memory.save_menu_route(identity(), ("Clientes", "Listado"))
    procedure_id = memory.save_procedure(identity(), ("Clientes", "Nuevo"), ("click", "click"))

    current = memory.list_current(identity())
    assert [(route.route_id, route.labels) for route in current.routes] == [
        (route_id, ("Clientes", "Listado"))
    ]
    assert [(item.procedure_id, item.labels, item.action_types) for item in current.procedures] == [
        (procedure_id, ("Clientes", "Nuevo"), ("click", "click"))
    ]
    assert memory.list_current(identity(screen_hash=HASH_B)).routes == ()
    assert memory.list_current(identity(title="Otra 2.0")).procedures == ()


def test_title_and_hash_are_normalized(tmp_path: Path) -> None:
    memory = AppMemory(tmp_path / "app_memory.sqlite3")
    memory.save_menu_route(identity("  Facturas   1.2 ", "A" * 16), ("Clientes",))
    current = memory.list_current(identity("facturas 1.2", HASH_A))
    assert [route.labels for route in current.routes] == [("Clientes",)]


def test_invalidated_items_are_omitted_and_can_be_relearned(tmp_path: Path) -> None:
    memory = AppMemory(tmp_path / "app_memory.sqlite3")
    route_id = memory.save_menu_route(identity(), ("Clientes",))
    procedure_id = memory.save_procedure(identity(), ("Clientes",), ("click",))
    memory.invalidate_route(route_id)
    memory.invalidate_procedure(procedure_id)
    assert memory.list_current(identity()) == MemoryHints(routes=(), procedures=())

    again = memory.save_menu_route(identity(), ("Clientes",))
    assert again != route_id
    assert [route.route_id for route in memory.list_current(identity()).routes] == [again]


def test_active_duplicate_is_not_stored_twice(tmp_path: Path) -> None:
    memory = AppMemory(tmp_path / "app_memory.sqlite3")
    first = memory.save_menu_route(identity(), ("Clientes", "Listado"))
    second = memory.save_menu_route(identity(), ("Clientes", "Listado"))
    assert first == second
    assert len(memory.list_current(identity()).routes) == 1


def test_registry_rejects_coordinates_amounts_and_marked_business_values(tmp_path: Path) -> None:
    memory = AppMemory(tmp_path / "app_memory.sqlite3")
    app = identity()
    with pytest.raises(MemoryRejected) as coordinates:
        memory.save_menu_route(app, ("Clientes", "120, 40"))
    with pytest.raises(MemoryRejected) as amount:
        memory.save_menu_route(app, ("Saldo $12.50",))
    with pytest.raises(MemoryRejected) as business:
        memory.save_menu_route(app, ("Abrir", "valor-campo"), business_values=("valor-campo",))
    assert "120" not in str(coordinates.value)
    assert "12.50" not in str(amount.value)
    assert "valor-campo" not in str(business.value)
    assert memory.list_current(app).routes == ()


def test_registry_rejects_amount_in_a_procedure_label(tmp_path: Path) -> None:
    memory = AppMemory(tmp_path / "app_memory.sqlite3")
    with pytest.raises(MemoryRejected):
        memory.save_procedure(identity(), ("Clientes", "1.250,00"), ("click", "type"))


def test_unknown_action_type_is_rejected(tmp_path: Path) -> None:
    memory = AppMemory(tmp_path / "app_memory.sqlite3")
    with pytest.raises(MemoryRejected):
        memory.save_procedure(identity(), ("Clientes",), ("type Ana",))


def test_store_is_not_the_task_table(tmp_path: Path) -> None:
    path = tmp_path / "app_memory.sqlite3"
    AppMemory(path)
    with sqlite3.connect(path) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        names = {row[0] for row in rows}
    assert "tasks" not in names
    assert {"menu_routes", "procedures"} <= names


def test_render_hints_keeps_few_short_lines(tmp_path: Path) -> None:
    memory = AppMemory(tmp_path / "app_memory.sqlite3")
    app = identity()
    for name in ("Uno", "Dos", "Tres"):
        memory.save_menu_route(app, (name,))
    memory.save_procedure(app, ("Clientes", "Nuevo"), ("click", "click"))
    current = memory.list_current(app)
    lines = render_hints(current, max_routes=2, max_procedures=1, max_chars=80)
    assert lines == ("Uno", "Dos", "Clientes > Nuevo: click, click")
    clipped = render_hints(current, max_routes=1, max_procedures=0, max_chars=2)
    assert clipped == ("U…",)


def test_memory_is_enabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMORY_ENABLED", raising=False)
    settings = Settings(OPENROUTER_API_KEY="k", TEAM_TOKEN="t", _env_file=None)  # type: ignore[call-arg]
    assert settings.MEMORY_ENABLED is True


def test_injected_path_is_the_only_file_created(tmp_path: Path) -> None:
    AppMemory(":memory:")
    memory = AppMemory(tmp_path / "nested" / "app_memory.sqlite3")
    memory.save_menu_route(identity(), ("Clientes",))
    assert (tmp_path / "nested" / "app_memory.sqlite3").is_file()
