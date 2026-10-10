"""SQLite task store. Holds only id, state, summary and timestamps: never the desktop
password or URL."""

import asyncio
import sqlite3
import threading
import time
from pathlib import Path

from aprendiz.models import TaskStatus

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    estado TEXT NOT NULL,
    resumen TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
)
"""


class TaskStore:
    def __init__(self, path: str) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.execute(_SCHEMA)

    def fail_unfinished(self, resumen: str) -> int:
        """Mark every task left `en_curso` by a previous process as failed."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE tasks SET estado='fallida', resumen=?, updated_at=? "
                "WHERE estado='en_curso'",
                (resumen, time.time()),
            )
            return cur.rowcount

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _create(self, task_id: str) -> None:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO tasks VALUES (?, 'en_curso', '', ?, ?)", (task_id, now, now)
            )

    def _set(self, task_id: str, status: TaskStatus) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE tasks SET estado=?, resumen=?, updated_at=? WHERE task_id=?",
                (status.estado, status.resumen, time.time(), task_id),
            )

    def _get(self, task_id: str) -> TaskStatus | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT estado, resumen FROM tasks WHERE task_id=?", (task_id,)
            ).fetchone()
        return TaskStatus(estado=row[0], resumen=row[1]) if row else None

    async def create(self, task_id: str) -> None:
        await asyncio.to_thread(self._create, task_id)

    async def set(self, task_id: str, status: TaskStatus) -> None:
        await asyncio.to_thread(self._set, task_id, status)

    async def get(self, task_id: str) -> TaskStatus | None:
        return await asyncio.to_thread(self._get, task_id)
