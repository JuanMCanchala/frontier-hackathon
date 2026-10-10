"""Verify paraphrased tests keep the same oracle and required literals."""

import json
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parent.parent / "eval"
ORIG_DIR = EVAL_DIR / "pruebas"
PARA_DIR = EVAL_DIR / "pruebas_parafrasis"

# Map paraphrased id -> original id
PV_MAP = {
    "PV1": "V1",
    "PV2": "V2",
    "PV3": "V3",
    "PV4": "V4",
    "PV5": "V5",
    "PV6": "V6",
    "PV7": "V7",
    "PV8": "V8",
    "PV9": "V9",
    "PV10": "V10",
    "PV11": "V11",
    "PV12": "V12",
}

# Required substrings that must remain in the paraphrased instruction
REQUIRED_LITERALS = {
    "PV1": ["NIT", "ciudad", "Abarrotes Quintero S.A.S."],
    "PV2": ["ayer"],
    "PV7": ["Distribuciones El Faro"],
    "PV9": ["Ana Torres"],
    "PV12": ["Ferretería Ruiz"],
}


def _load(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _orig_path(para_id: str) -> Path:
    oid = PV_MAP[para_id]
    suffix = {
        "V1": "pregunta-dos-datos",
        "V2": "pregunta-ayer",
        "V3": "editar-manana",
        "V4": "cliente-inexistente",
        "V5": "borrado-masivo",
        "V6": "nota-inyectada-edicion",
        "V7": "nota-inyectada-pregunta",
        "V8": "cliente-duplicado",
        "V9": "homonimos-pregunta",
        "V10": "homonimos-con-documento",
        "V11": "exportar-otros-filtros",
        "V12": "pregunta-sin-respuesta",
    }[oid]
    return ORIG_DIR / f"{oid}-{suffix}.json"


def _para_path(para_id: str) -> Path:
    suffix = {
        "PV1": "pregunta-dos-datos",
        "PV2": "pregunta-ayer",
        "PV3": "editar-manana",
        "PV4": "cliente-inexistente",
        "PV5": "borrado-masivo",
        "PV6": "nota-inyectada-edicion",
        "PV7": "nota-inyectada-pregunta",
        "PV8": "cliente-duplicado",
        "PV9": "homonimos-pregunta",
        "PV10": "homonimos-con-documento",
        "PV11": "exportar-otros-filtros",
        "PV12": "pregunta-sin-respuesta",
    }[para_id]
    return PARA_DIR / f"{para_id}-{suffix}.json"


def _discover() -> list[str]:
    discovered = []
    for path in sorted(PARA_DIR.glob("PV*.json")):
        discovered.append(path.stem.split("-")[0])
    return discovered


@pytest.fixture(scope="module")
def discovered() -> list[str]:
    return _discover()


def pytest_generate_tests(metafunc):
    if "pid" in metafunc.fixturenames and "oid" not in metafunc.fixturenames:
        if "discovered" in metafunc.fixturenames:
            return
        discovered = _discover()
        metafunc.parametrize("pid", discovered, ids=discovered)


class TestOracle:
    def _load_pair(self, pid: str):
        para = _load(_para_path(pid))
        orig = _load(_orig_path(pid))
        return para, orig

    def test_estado(self, pid: str) -> None:
        para, orig = self._load_pair(pid)
        assert para["estado_esperado"] == orig["estado_esperado"]

    def test_diff(self, pid: str) -> None:
        para, orig = self._load_pair(pid)
        assert para["diff_esperado"] == orig["diff_esperado"]

    def test_lista_blanca(self, pid: str) -> None:
        para, orig = self._load_pair(pid)
        assert para["lista_blanca"] == orig["lista_blanca"]

    def test_nivel(self, pid: str) -> None:
        para, orig = self._load_pair(pid)
        assert para.get("nivel") == orig.get("nivel")

    def test_respuesta(self, pid: str) -> None:
        para, orig = self._load_pair(pid)
        assert para.get("respuesta_esperada") == orig.get("respuesta_esperada")

    def test_claves(self, pid: str) -> None:
        para, orig = self._load_pair(pid)
        assert para.get("claves_razon") == orig.get("claves_razon")

    def test_instruction_changed(self, pid: str) -> None:
        para, orig = self._load_pair(pid)
        assert para["instruccion"] != orig["instruccion"]

    def test_required_literals_present(self, pid: str) -> None:
        para, orig = self._load_pair(pid)
        instruction = para["instruccion"]
        for literal in REQUIRED_LITERALS.get(pid, []):
            assert literal in instruction, f"{pid} missing required literal: {literal}"