"""HTTP server for the mini CRUD app (stdlib only)."""

import json
import logging
import os
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from mini_crud import config
from mini_crud.page import render
from mini_crud.store import Store

log = logging.getLogger(__name__)

_STORE = Store()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        log.info("%s - %s", self.address_string(), format % args)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            body = render(_STORE).encode("utf-8")
            self._send(200, body, "text/html; charset=utf-8")
            return
        if parsed.path == "/registros":
            query = parse_qs(parsed.query).get("q", [""])[0]
            payload = [_record_dict(r) for r in _STORE.search(query)]
            self._send_json(200, payload)
            return
        self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw.decode("utf-8") or "{}") if raw else {}
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid json"})
            return

        if parsed.path == "/reiniciar":
            _reiniciar_y_recargar()
            self._send_json(200, {"ok": True})
            return
        if parsed.path == "/registros":
            record = _STORE.create(
                str(data.get("name", "")),
                str(data.get("bench", "")),
                str(data.get("note", "")),
            )
            self._send_json(201, _record_dict(record))
            return
        if parsed.path.endswith("/borrar"):
            record_id = _segment_id(parsed.path, "/borrar")
            try:
                _STORE.delete(record_id)
            except KeyError:
                self._send_json(404, {"error": "not found"})
                return
            self._send_json(200, {"ok": True})
            return
        if parsed.path.endswith("/celda"):
            record_id = _segment_id(parsed.path, "/celda")
            try:
                record = _STORE.commit_cell(
                    record_id,
                    str(data.get("field", "")),
                    str(data.get("value", "")),
                )
            except (KeyError, ValueError) as exc:
                self._send_json(400, {"error": str(exc)})
                return
            self._send_json(200, _record_dict(record))
            return
        self._send_json(404, {"error": "not found"})

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: object) -> None:
        body = json.dumps(payload).encode("utf-8")
        self._send(status, body, "application/json")


def _segment_id(path: str, suffix: str) -> str:
    prefix = "/registros/"
    if not path.startswith(prefix) or not path.endswith(suffix):
        raise ValueError("bad path")
    middle = path[len(prefix) : -len(suffix)]
    return middle.strip("/")


def _record_dict(record: object) -> dict[str, str]:
    from mini_crud.store import Record

    if not isinstance(record, Record):
        raise TypeError("expected Record")
    return {"id": record.id, "name": record.name, "bench": record.bench, "note": record.note}


def _reiniciar_y_recargar() -> None:
    """Close any open native dialogs, reset the store, and reload the browser.

    Strategy: Escape closes prompt()/confirm()/alert() on Chromium.  A brief
    pause lets any accidental POST from ``prompt() || ""`` land so the store
    reset that follows wipes those side effects.  Ctrl+r then reloads the
    kiosk page so the next test starts with a clean DOM.
    """
    display = os.environ.get("DISPLAY", ":1")
    env = {"DISPLAY": display}

    # 1. Close any native dialog still open from the previous task.
    subprocess.run(["xdotool", "key", "Escape"], env=env, timeout=5)
    # 2. Wait for any in-flight POST triggered by the prompt closing.
    time.sleep(0.3)
    # 3. Wipe the store (undoes any partial record from step 1).
    _STORE.reset()
    # 4. Reload the browser page.
    subprocess.run(["xdotool", "key", "ctrl+r"], env=env, timeout=5)
    # 5. Let the page render settle before the harness takes the before snapshot.
    time.sleep(2.0)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    server = ThreadingHTTPServer((config.HTTP_HOST, config.HTTP_PORT), _Handler)
    log.info("listening on http://%s:%s", config.HTTP_HOST, config.HTTP_PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
