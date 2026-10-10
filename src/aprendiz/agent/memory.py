"""Per-app SQLite memory of observed menu routes and verified procedures.

Kept apart from the task store so a bad hint cannot rewrite task state. What is
stored is a hint for a later actor, never a sequence to replay and never a
coordinate or a business value.
"""

import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import get_args

from aprendiz.agent.schemas import TipoAccion

log = logging.getLogger(__name__)

# 64-bit dhash as hex. Same width as the perceptual hash used to tell screens apart.
_HASH = re.compile(r"^[0-9a-f]{16}$")
_ACTION_TYPES = set(get_args(TipoAccion))
_COORDINATES = re.compile(
    r"\d{1,4}\s*[,;]\s*\d{1,4}|\b[xy]\s*[:=]\s*\d{1,4}\b|\d{2,4}\s*[x×]\s*\d{2,4}",
    re.IGNORECASE,
)
_AMOUNT = re.compile(
    r"[$€£]|\b(?:usd|eur|cop)\b|\b\d{1,3}(?:\.\d{3})+,\d{2}\b|\b\d{1,3}(?:,\d{3})+\.\d{2}\b|\b\d+[.,]\d{2}\b",
    re.IGNORECASE,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS menu_routes (
    route_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    screen_hash TEXT NOT NULL,
    labels_json TEXT NOT NULL,
    invalidated INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS procedures (
    procedure_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    screen_hash TEXT NOT NULL,
    labels_json TEXT NOT NULL,
    actions_json TEXT NOT NULL,
    invalidated INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
"""


class MemoryRejected(ValueError):
    """The text is not safe to keep as an app hint. The message names the category only."""


@dataclass(frozen=True)
class AppIdentity:
    title: str
    screen_hash: str


@dataclass(frozen=True)
class MenuRoute:
    route_id: str
    labels: tuple[str, ...]


@dataclass(frozen=True)
class Procedure:
    procedure_id: str
    labels: tuple[str, ...]
    action_types: tuple[str, ...]


@dataclass(frozen=True)
class MemoryHints:
    routes: tuple[MenuRoute, ...]
    procedures: tuple[Procedure, ...]


def _normalize_title(title: str) -> str:
    return " ".join(title.casefold().split())


def _identity_key(identity: AppIdentity) -> tuple[str, str]:
    title = _normalize_title(identity.title)
    digest = identity.screen_hash.strip().lower()
    if not title or _HASH.fullmatch(digest) is None:
        raise MemoryRejected("identity")
    return title, digest


def _clean_label(label: str) -> str:
    text = label.strip()
    if not text:
        raise MemoryRejected("empty")
    return text


def _reject(text: str, business_values: Sequence[str]) -> None:
    if _COORDINATES.search(text):
        log.warning("app memory rejected content: coordinates")
        raise MemoryRejected("coordinates")
    if _AMOUNT.search(text):
        log.warning("app memory rejected content: amount")
        raise MemoryRejected("amount")
    folded = " ".join(text.casefold().split())
    for value in business_values:
        marked = " ".join(value.casefold().split())
        if marked and marked in folded:
            log.warning("app memory rejected content: business_data")
            raise MemoryRejected("business_data")


def render_hints(
    hints: MemoryHints,
    *,
    max_routes: int,
    max_procedures: int,
    max_chars: int,
) -> tuple[str, ...]:
    """Short lines for the actor. Caps keep a hint from becoming a script."""

    def clip(text: str) -> str:
        if len(text) <= max_chars:
            return text
        if max_chars <= 1:
            return "…"
        return text[: max_chars - 1] + "…"

    lines = [" > ".join(route.labels) for route in hints.routes[:max_routes]]
    for procedure in hints.procedures[:max_procedures]:
        path = " > ".join(procedure.labels)
        actions = ", ".join(procedure.action_types)
        lines.append(f"{path}: {actions}" if path else actions)
    return tuple(clip(line) for line in lines)


class AppMemory:
    """Routes and procedures for one app identity. An invalidated row stays, but is not listed."""

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def save_menu_route(
        self,
        identity: AppIdentity,
        labels: Sequence[str],
        *,
        business_values: Sequence[str] = (),
    ) -> str:
        title, digest = _identity_key(identity)
        cleaned = tuple(_clean_label(label) for label in labels)
        if not cleaned:
            raise MemoryRejected("empty")
        for label in cleaned:
            _reject(label, business_values)
        payload = json.dumps(cleaned, ensure_ascii=False)
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT route_id FROM menu_routes "
                "WHERE title=? AND screen_hash=? AND labels_json=? AND invalidated=0",
                (title, digest, payload),
            ).fetchone()
            if row:
                return str(row[0])
            route_id = uuid.uuid4().hex
            self._conn.execute(
                "INSERT INTO menu_routes VALUES (?, ?, ?, ?, 0, ?)",
                (route_id, title, digest, payload, time.time()),
            )
        return route_id

    def save_procedure(
        self,
        identity: AppIdentity,
        labels: Sequence[str],
        action_types: Sequence[str],
        *,
        business_values: Sequence[str] = (),
    ) -> str:
        title, digest = _identity_key(identity)
        cleaned = tuple(_clean_label(label) for label in labels)
        actions = tuple(action_types)
        if not actions:
            raise MemoryRejected("empty")
        for label in cleaned:
            _reject(label, business_values)
        for action in actions:
            if action not in _ACTION_TYPES:
                log.warning("app memory rejected content: action_type")
                raise MemoryRejected("action_type")
        labels_payload = json.dumps(cleaned, ensure_ascii=False)
        actions_payload = json.dumps(actions, ensure_ascii=False)
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT procedure_id FROM procedures "
                "WHERE title=? AND screen_hash=? AND labels_json=? AND actions_json=? "
                "AND invalidated=0",
                (title, digest, labels_payload, actions_payload),
            ).fetchone()
            if row:
                return str(row[0])
            procedure_id = uuid.uuid4().hex
            self._conn.execute(
                "INSERT INTO procedures VALUES (?, ?, ?, ?, ?, 0, ?)",
                (procedure_id, title, digest, labels_payload, actions_payload, time.time()),
            )
        return procedure_id

    def list_current(self, identity: AppIdentity) -> MemoryHints:
        title, digest = _identity_key(identity)
        with self._lock:
            routes = self._conn.execute(
                "SELECT route_id, labels_json FROM menu_routes "
                "WHERE title=? AND screen_hash=? AND invalidated=0 ORDER BY created_at",
                (title, digest),
            ).fetchall()
            procedures = self._conn.execute(
                "SELECT procedure_id, labels_json, actions_json FROM procedures "
                "WHERE title=? AND screen_hash=? AND invalidated=0 ORDER BY created_at",
                (title, digest),
            ).fetchall()
        return MemoryHints(
            routes=tuple(
                MenuRoute(route_id, tuple(json.loads(payload))) for route_id, payload in routes
            ),
            procedures=tuple(
                Procedure(procedure_id, tuple(json.loads(labels)), tuple(json.loads(actions)))
                for procedure_id, labels, actions in procedures
            ),
        )

    def invalidate_route(self, route_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE menu_routes SET invalidated=1 WHERE route_id=?", (route_id,)
            )

    def invalidate_procedure(self, procedure_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE procedures SET invalidated=1 WHERE procedure_id=?", (procedure_id,)
            )


class MemorySession:
    """One task's notes. A store error is logged and swallowed so the task can continue."""

    def __init__(
        self,
        store: AppMemory | None,
        identity: AppIdentity | None,
        *,
        hints: tuple[str, ...] = (),
        used_procedure_ids: Sequence[str] = (),
        business_values: Sequence[str] = (),
    ) -> None:
        self.hints = hints
        self._store = store
        self._identity = identity
        self._used = tuple(used_procedure_ids)
        self._business = [value for value in business_values if value.strip()]
        self._labels: list[str] = []
        self._actions: list[str] = []

    def remember_business(self, value: str) -> None:
        if value.strip():
            self._business.append(value)

    def note_navigation(self, label: str) -> None:
        text = label.strip()
        store, identity = self._store, self._identity
        if not text or store is None or identity is None:
            return
        try:
            store.save_menu_route(identity, (text,), business_values=self._business)
        except MemoryRejected:
            log.warning("app memory rejected content: navigation")
            return
        except (sqlite3.Error, OSError) as exc:
            log.warning("app memory unavailable", extra={"error": type(exc).__name__})
            return
        self._labels.append(text)

    def note_action(self, action_type: str) -> None:
        if action_type in _ACTION_TYPES:
            self._actions.append(action_type)

    def save_verified(self) -> None:
        store, identity = self._store, self._identity
        if not self._actions or store is None or identity is None:
            return
        self._write(
            lambda: store.save_procedure(
                identity,
                tuple(self._labels),
                tuple(self._actions),
                business_values=self._business,
            )
        )

    def invalidate_used(self) -> None:
        store = self._store
        if store is None:
            return
        for procedure_id in self._used:
            self._write(lambda procedure_id=procedure_id: store.invalidate_procedure(procedure_id))

    def close(self) -> None:
        if self._store is None:
            return
        try:
            self._store.close()
        except sqlite3.Error as exc:
            log.warning("app memory unavailable", extra={"error": type(exc).__name__})

    def _write(self, fn) -> None:
        try:
            fn()
        except MemoryRejected:
            log.warning("app memory rejected content: procedure")
        except (sqlite3.Error, OSError) as exc:
            log.warning("app memory unavailable", extra={"error": type(exc).__name__})
