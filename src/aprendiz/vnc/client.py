"""Async RFB 3.8 client over a websocket (binary subprotocol)."""

import asyncio
import logging
import struct
import time
from types import TracebackType
from urllib.parse import urlsplit

from Crypto.Cipher import DES
from PIL import Image
from websockets.asyncio.client import ClientConnection, connect

from aprendiz.config import VncTimings
from aprendiz.vnc.errors import VncError
from aprendiz.vnc.keys import KEYSYM_RETURN, KEYSYM_TAB, keysym_for_char, parse_combo
from aprendiz.vnc.lease import Lease

log = logging.getLogger(__name__)

ENC_RAW = 0
ENC_DESKTOP_SIZE = -223

_PIXEL_FORMAT = struct.pack(">BBBBHHHBBB3x", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)

_BUTTON_MASK = {"left": 1, "middle": 2, "right": 4}
_WHEEL_UP = 8
_WHEEL_DOWN = 16


def vnc_auth_response(challenge: bytes, password: str) -> bytes:
    """DES-ECB of the 16-byte challenge; each key byte has its bits reversed."""
    key = password.encode("latin-1", errors="replace")[:8].ljust(8, b"\x00")
    key = bytes(int(f"{b:08b}"[::-1], 2) for b in key)
    return DES.new(key, DES.MODE_ECB).encrypt(challenge)


class VncClient:
    def __init__(self, timings: VncTimings | None = None, lease: Lease | None = None) -> None:
        self._t = timings or VncTimings()
        self._lease = lease
        self._mask = 0
        self._pointer_pos = (0, 0)
        self._keys_down: set[int] = set()
        self._ws: ClientConnection | None = None
        self._buf = bytearray()
        self._incoming = asyncio.Event()
        self._reader: asyncio.Task[None] | None = None
        self._fb: Image.Image | None = None
        self._last_change = 0.0
        self._first_update = asyncio.Event()
        self._reader_error: BaseException | None = None

    async def __aenter__(self) -> "VncClient":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    # ---- connection ------------------------------------------------------

    async def connect(self, url: str, password: str) -> None:
        # Only the host is logged: the URL may carry credentials in its query.
        host = urlsplit(url).netloc
        log.info("vnc connect", extra={"host": host})
        try:
            async with asyncio.timeout(self._t.connect_timeout_s):
                self._ws = await connect(url, subprotocols=["binary"], max_size=None)
                await self._handshake(password)
                self._reader = asyncio.create_task(self._read_loop())
                await self._request_update(incremental=False)
                await self._first_update.wait()
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
            self._reader = None
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def force_close(self) -> None:
        """Release held input, then close the websocket, aborting if it does not close fast."""
        await self.release_all()
        if self._reader is not None:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
            self._reader = None
        ws, self._ws = self._ws, None
        if ws is None:
            return
        try:
            await asyncio.wait_for(ws.close(), self._t.force_close_timeout_s)
        except Exception:
            log.warning("vnc close handshake failed, aborting transport")
            ws.transport.abort()

    async def release_all(self) -> None:
        """Send button-up and key-up for everything held. Allowed with a revoked lease:
        releasing changes nothing in the app and avoids a stuck button or modifier."""
        if self._ws is None or (self._mask == 0 and not self._keys_down):
            return
        try:
            if self._mask:
                x, y = self._pointer_pos
                self._mask = 0
                await self._send(struct.pack(">BBHH", 5, 0, x, y))
            for keysym in reversed(list(self._keys_down)):
                self._keys_down.discard(keysym)
                await self._send(struct.pack(">BBxxI", 4, 0, keysym))
        except Exception:
            log.warning("vnc release_all failed", exc_info=True)

    async def _handshake(self, password: str) -> None:
        version = await self._read(12)
        if not version.startswith(b"RFB 003."):
            raise VncError(f"unexpected server version: {version!r}")
        await self._send(b"RFB 003.008\n")

        count = (await self._read(1))[0]
        if count == 0:
            raise VncError(f"server refused connection: {await self._read_reason()}")
        types = await self._read(count)
        if 2 not in types:
            raise VncError(f"server does not offer VNC authentication: {list(types)}")
        await self._send(bytes([2]))
        challenge = await self._read(16)
        await self._send(vnc_auth_response(challenge, password))
        (result,) = struct.unpack(">I", await self._read(4))
        if result != 0:
            raise VncError(f"authentication failed: {await self._read_reason()}")

        await self._send(bytes([1]))  # ClientInit: shared
        width, height = struct.unpack(">HH", await self._read(4))
        await self._read(16)  # server pixel format, replaced below
        (name_len,) = struct.unpack(">I", await self._read(4))
        await self._read(name_len)
        self._fb = Image.new("RGB", (width, height))

        await self._send(struct.pack(">B3x", 0) + _PIXEL_FORMAT)
        encodings = [ENC_RAW, ENC_DESKTOP_SIZE]
        await self._send(struct.pack(f">BxH{len(encodings)}i", 2, len(encodings), *encodings))

    async def _read_reason(self) -> str:
        (length,) = struct.unpack(">I", await self._read(4))
        return (await self._read(length)).decode("utf-8", errors="replace")

    # ---- transport -------------------------------------------------------

    async def _send(self, data: bytes) -> None:
        if self._ws is None:
            raise VncError("not connected")
        await self._ws.send(data)

    async def _read(self, n: int) -> bytes:
        """Read exactly n bytes; websocket frames do not align with RFB messages."""
        if self._ws is None:
            raise VncError("not connected")
        while len(self._buf) < n:
            chunk = await self._ws.recv()
            if isinstance(chunk, str):
                raise VncError("unexpected text frame from server")
            self._buf += chunk
        data = bytes(self._buf[:n])
        del self._buf[:n]
        return data

    # ---- reader task -----------------------------------------------------

    async def _read_loop(self) -> None:
        try:
            while True:
                (msg_type,) = await self._read(1)
                if msg_type == 0:
                    await self._on_framebuffer_update()
                elif msg_type == 1:
                    await self._on_colour_map()
                elif msg_type == 2:
                    log.debug("vnc bell")
                elif msg_type == 3:
                    await self._on_cut_text()
                else:
                    raise VncError(f"unknown server message type {msg_type}")
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._reader_error = exc
            log.error("vnc reader stopped: %s", exc)
            self._first_update.set()
            raise

    async def _on_framebuffer_update(self) -> None:
        _, count = struct.unpack(">BH", await self._read(3))
        for _ in range(count):
            x, y, w, h, enc = struct.unpack(">HHHHi", await self._read(12))
            if enc == ENC_RAW:
                self._paint_raw(x, y, w, h, await self._read(w * h * 4))
            elif enc == ENC_DESKTOP_SIZE:
                self._resize(w, h)
            else:
                raise VncError(f"unsupported encoding {enc}")
        self._first_update.set()
        await self._request_update(incremental=True)

    def _paint_raw(self, x: int, y: int, w: int, h: int, data: bytes) -> None:
        fb = self._require_fb()
        if x + w > fb.width or y + h > fb.height:
            raise VncError(f"rect {x},{y} {w}x{h} outside framebuffer {fb.size}")
        if w == 0 or h == 0:
            return
        patch = Image.frombytes("RGB", (w, h), data, "raw", "BGRX")
        box = (x, y, x + w, y + h)
        if fb.crop(box).tobytes() != patch.tobytes():
            fb.paste(patch, box)
            self._last_change = time.monotonic()

    def _resize(self, w: int, h: int) -> None:
        old = self._require_fb()
        new = Image.new("RGB", (w, h))
        new.paste(old, (0, 0))
        self._fb = new
        self._last_change = time.monotonic()

    async def _on_colour_map(self) -> None:
        # True colour is negotiated, so palettes are consumed but unused.
        _, _first, count = struct.unpack(">BHH", await self._read(5))
        await self._read(count * 6)

    async def _on_cut_text(self) -> None:
        (length,) = struct.unpack(">3xI", await self._read(7))
        await self._read(length)

    async def _request_update(self, incremental: bool) -> None:
        fb = self._require_fb()
        await self._send(struct.pack(">BBHHHH", 3, int(incremental), 0, 0, fb.width, fb.height))

    def _require_fb(self) -> Image.Image:
        if self._fb is None:
            raise VncError("not connected")
        return self._fb

    def _check_reader(self) -> None:
        if self._reader_error is not None:
            raise VncError(f"connection lost: {self._reader_error}") from self._reader_error

    # ---- screen ----------------------------------------------------------

    async def wait_stable(self) -> None:
        """Wait until the framebuffer is unchanged for the quiet window, or time out."""
        start = time.monotonic()
        deadline = start + self._t.stable_timeout_s
        while True:
            self._check_reader()
            now = time.monotonic()
            quiet_for = now - max(self._last_change, start)
            if quiet_for >= self._t.stable_quiet_s:
                return
            if now >= deadline:
                log.warning("vnc wait_stable timed out")
                return
            await asyncio.sleep(0.02)

    async def screenshot(self) -> Image.Image:
        await self.wait_stable()
        return self._require_fb().copy()

    # ---- input -----------------------------------------------------------

    def _ensure_lease(self) -> None:
        if self._lease is not None:
            self._lease.ensure_active()

    async def _pointer(self, x: int, y: int, mask: int) -> None:
        self._ensure_lease()
        self._mask, self._pointer_pos = mask, (x, y)
        await self._send(struct.pack(">BBHH", 5, mask, x, y))

    async def _key_event(self, keysym: int, down: bool) -> None:
        self._ensure_lease()
        if down:
            self._keys_down.add(keysym)
        else:
            self._keys_down.discard(keysym)
        await self._send(struct.pack(">BBxxI", 4, int(down), keysym))

    async def move(self, x: int, y: int) -> None:
        self._check_reader()
        await self._glide(x, y)

    async def _glide(self, x: int, y: int) -> None:
        """Travel to the point like a mouse: along the current row first, then down or up, and
        pause on it. A pointer that teleports and presses at once loses hover menus: the
        submenu closes before the browser sees the pointer over its item. The row-first path
        goes from a parent item straight into its submenu without crossing its siblings."""
        sx, sy = self._pointer_pos
        steps = self._t.glide_steps
        if steps and abs(x - sx) + abs(y - sy) > 24:
            path = [(sx + (x - sx) * i // steps, sy) for i in range(1, steps + 1)]
            path += [(x, sy + (y - sy) * i // steps) for i in range(1, steps)]
            for px, py in path:
                await self._pointer(px, py, 0)
                await asyncio.sleep(self._t.glide_step_s)
        await self._pointer(x, y, 0)
        await asyncio.sleep(self._t.hover_settle_s)

    async def click(self, x: int, y: int, button: str = "left", double: bool = False) -> None:
        self._check_reader()
        if button not in _BUTTON_MASK:
            raise ValueError(f"unknown button: {button!r}")
        mask = _BUTTON_MASK[button]
        await self._glide(x, y)
        for i in range(2 if double else 1):
            if i:
                await asyncio.sleep(self._t.double_click_gap_s)
            await self._pointer(x, y, mask)
            await asyncio.sleep(self._t.click_hold_s)
            await self._pointer(x, y, 0)

    async def scroll(self, x: int, y: int, dy: int) -> None:
        """Positive dy scrolls down, negative up; one wheel notch per unit."""
        self._check_reader()
        wheel = _WHEEL_DOWN if dy > 0 else _WHEEL_UP
        await self._pointer(x, y, 0)
        for _ in range(abs(dy)):
            await self._pointer(x, y, wheel)
            await self._pointer(x, y, 0)

    async def key(self, combo: str) -> None:
        self._check_reader()
        keysyms = parse_combo(combo)
        for keysym in keysyms:
            await self._key_event(keysym, True)
        for keysym in reversed(keysyms):
            await self._key_event(keysym, False)
        await asyncio.sleep(self._t.key_delay_s)

    async def type_text(self, text: str) -> None:
        self._check_reader()
        for char in text:
            if char == "\n":
                keysym = KEYSYM_RETURN
            elif char == "\t":
                keysym = KEYSYM_TAB
            else:
                keysym = keysym_for_char(char)
            await self._key_event(keysym, True)
            await self._key_event(keysym, False)
            # Accented characters were occasionally dropped when typed at full speed.
            pause = self._t.non_ascii_delay_s if ord(char) > 0x7F else self._t.key_delay_s
            await asyncio.sleep(pause)
