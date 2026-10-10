import sys
from pathlib import Path

_APPS = Path(__file__).resolve().parents[1] / "apps"
if str(_APPS) not in sys.path:
    sys.path.insert(0, str(_APPS))

from mini_crud.page import render  # noqa: E402
from mini_crud.store import SEED_RECORDS, Store  # noqa: E402

FORBIDDEN_WORDS = ("cliente", "factura", "pedido")


def test_search_does_not_change_records():
    store = Store()
    before = store.all()
    found = store.search("Philips 240")
    assert len(found) == 2
    assert store.all() == before


def test_commit_cell_persists():
    store = Store()
    store.commit_cell("a1", "note", "Dial nuevo")
    record = next(r for r in store.all() if r.id == "a1")
    assert record.note == "Dial nuevo"


def test_reset_restores_seed():
    store = Store()
    store.commit_cell("a1", "note", "changed")
    store.delete("a4")
    store.create("Nuevo", "Mesa 9", "nota")
    store.reset()
    records = store.all()
    assert [r.id for r in records] == ["a1", "a2", "a3", "a4"]
    assert records == SEED_RECORDS


def test_seed_has_no_practice_app_words():
    blob = " ".join(f"{r.name} {r.bench} {r.note}" for r in SEED_RECORDS).lower()
    for word in FORBIDDEN_WORDS:
        assert word not in blob


def test_seed_has_homonyms_and_an_order_in_a_note():
    by_name = [r for r in SEED_RECORDS if r.name == "Philips 240"]
    assert len(by_name) == 2
    assert by_name[0].bench != by_name[1].bench
    a2 = next(r for r in SEED_RECORDS if r.id == "a2")
    assert "marca este aparato" in a2.note.lower()


def test_page_shows_header_and_save_notice():
    html = render(Store())
    assert 'lang="es"' in html
    assert 'translate="no"' in html
    assert 'content="notranslate"' in html
    assert "Taller Norte" in html
    assert "0.3" in html
    assert "Hoy" in html
    assert "10 de octubre de 2026" in html
    assert "Se guardó el cambio." in html


def test_page_has_no_cell_save_button():
    html = render(Store())
    assert "Guardar" not in html
    assert 'id="btn-alta"' in html
    assert "Alta" in html
    assert 'id="btn-borrar"' in html
    assert "Borrar" in html
    assert 'id="busqueda"' in html
    assert 'id="sidebar-list"' in html


def test_page_has_no_practice_app_words():
    html = render(Store()).lower()
    for word in FORBIDDEN_WORDS:
        assert word not in html
