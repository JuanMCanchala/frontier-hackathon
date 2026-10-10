"""Minimal RFB 3.8 server over websocket, recording what the client sends."""

import os
import struct
import time
from dataclasses import dataclass, field

from Crypto.Cipher import DES
from websockets.asyncio.server import Server, ServerConnection, serve

PASSWORD = "secret"


def expected_response(challenge: bytes, password: str) -> bytes:
    # Independent of the client code: reverse key bits with a table-free loop.
    key = bytearray(8)
    for i, b in enumerate(password.encode().ljust(8, b"\0")[:8]):
        r = 0
        for bit in range(8):
            if b & (1 << bit):
                r |= 1 << (7 - bit)
        key[i] = r
    return DES.new(bytes(key), DES.MODE_ECB).encrypt(challenge)


@dataclass
class FakeRfbServer:
    width: int = 64
    height: int = 48
    password: str = PASSWORD
    challenge: bytes = field(default_factory=lambda: os.urandom(16))
    handshake_log: list[str] = field(default_factory=list)
    auth_ok: bool = False
    shared_flag: int | None = None
    pixel_format: bytes = b""
    encodings: list[int] = field(default_factory=list)
    pointer_events: list[tuple[float, bytes]] = field(default_factory=list)
    key_events: list[bytes] = field(default_factory=list)
    key_times: list[float] = field(default_factory=list)
    update_requests: list[bytes] = field(default_factory=list)
    _buf: bytearray = field(default_factory=bytearray)
    _ws: ServerConnection | None = None
    _server: Server | None = None
    port: int = 0
    closed_at: float | None = None

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    async def start(self) -> None:
        self._server = await serve(self._handle, "127.0.0.1", 0, subprotocols=["binary"])
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    async def _read(self, n: int) -> bytes:
        assert self._ws is not None
        while len(self._buf) < n:
            self._buf += await self._ws.recv()
        data = bytes(self._buf[:n])
        del self._buf[:n]
        return data

    async def _handle(self, ws: ServerConnection) -> None:
        try:
            await self._serve_connection(ws)
        finally:
            self.closed_at = time.monotonic()

    async def _serve_connection(self, ws: ServerConnection) -> None:
        self._ws = ws
        self._buf.clear()
        await ws.send(b"RFB 003.008\n")
        self.handshake_log.append((await self._read(12)).decode())
        await ws.send(bytes([2, 1, 2]))  # offers None and VNC auth
        self.handshake_log.append(f"security={(await self._read(1))[0]}")
        await ws.send(self.challenge)
        response = await self._read(16)
        self.auth_ok = response == expected_response(self.challenge, self.password)
        if not self.auth_ok:
            reason = b"bad password"
            await ws.send(struct.pack(">II", 1, len(reason)) + reason)
            return
        await ws.send(struct.pack(">I", 0))
        self.shared_flag = (await self._read(1))[0]
        name = b"fake"
        server_format = struct.pack(">BBBBHHHBBB3x", 16, 16, 0, 1, 31, 63, 31, 11, 5, 0)
        await ws.send(
            struct.pack(">HH", self.width, self.height)
            + server_format
            + struct.pack(">I", len(name))
            + name
        )
        await self._message_loop()

    async def _message_loop(self) -> None:
        while True:
            msg_type = (await self._read(1))[0]
            if msg_type == 0:
                self.pixel_format = (await self._read(19))[3:]
            elif msg_type == 2:
                (count,) = struct.unpack(">xH", await self._read(3))
                self.encodings = list(struct.unpack(f">{count}i", await self._read(4 * count)))
            elif msg_type == 3:
                request = await self._read(9)
                self.update_requests.append(request)
                if request[0] == 0:
                    await self.send_rect(0, 0, self.width, self.height, (0, 0, 0))
            elif msg_type == 4:
                self.key_events.append(await self._read(7))
                self.key_times.append(time.monotonic())
            elif msg_type == 5:
                self.pointer_events.append((time.monotonic(), await self._read(5)))
            else:
                raise AssertionError(f"unexpected client message {msg_type}")

    async def send_rect(self, x: int, y: int, w: int, h: int, rgb: tuple[int, int, int]) -> None:
        assert self._ws is not None
        r, g, b = rgb
        pixels = bytes([b, g, r, 0]) * (w * h)
        header = struct.pack(">BxH", 0, 1) + struct.pack(">HHHHi", x, y, w, h, 0)
        await self._ws.send(header + pixels)

    async def send_raw(self, data: bytes) -> None:
        assert self._ws is not None
        await self._ws.send(data)
