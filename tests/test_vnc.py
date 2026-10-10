import asyncio
import struct
from collections.abc import AsyncIterator

import pytest

from aprendiz.config import VncTimings
from aprendiz.vnc import VncClient, VncError
from tests.fake_server import PASSWORD, FakeRfbServer

FAST = VncTimings(key_delay_s=0.001, stable_quiet_s=0.1, stable_timeout_s=1.0)


@pytest.fixture
async def server() -> AsyncIterator[FakeRfbServer]:
    srv = FakeRfbServer()
    await srv.start()
    yield srv
    await srv.stop()


@pytest.fixture
async def client(server: FakeRfbServer) -> AsyncIterator[VncClient]:
    async with VncClient(FAST) as c:
        await c.connect(server.url, PASSWORD)
        yield c


async def settle() -> None:
    await asyncio.sleep(0.1)


async def test_handshake_and_setup(server: FakeRfbServer, client: VncClient) -> None:
    await settle()
    assert server.handshake_log == ["RFB 003.008\n", "security=2"]
    assert server.auth_ok
    assert server.shared_flag == 1
    assert server.pixel_format == struct.pack(
        ">BBBBHHHBBB3x", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0
    )
    assert server.encodings == [0, -223]
    assert server.update_requests[0] == struct.pack(">BHHHH", 0, 0, 0, 64, 48)


async def test_wrong_password_fails(server: FakeRfbServer) -> None:
    async with VncClient(FAST) as c:
        with pytest.raises(VncError, match="authentication failed: bad password"):
            await c.connect(server.url, "wrong")
    assert not server.auth_ok


async def test_pointer_event_bytes(server: FakeRfbServer, client: VncClient) -> None:
    await client.move(300, 200)
    await client.click(10, 20, button="right")
    await settle()
    events = [e for _, e in server.pointer_events]
    assert events[0] == bytes([0]) + struct.pack(">HH", 300, 200)
    assert events[1:4] == [
        bytes([0]) + struct.pack(">HH", 10, 20),
        bytes([4]) + struct.pack(">HH", 10, 20),
        bytes([0]) + struct.pack(">HH", 10, 20),
    ]


async def test_click_holds_at_least_150ms(server: FakeRfbServer, client: VncClient) -> None:
    await client.click(5, 5)
    await settle()
    press = next(t for t, e in server.pointer_events if e[0] == 1)
    release = next(t for t, e in server.pointer_events if e[0] == 0 and t > press)
    assert release - press >= 0.14  # 10 ms tolerance for scheduling on the server side


async def test_double_click_and_scroll(server: FakeRfbServer, client: VncClient) -> None:
    await client.click(1, 2, double=True)
    await client.scroll(3, 4, 2)
    await client.scroll(3, 4, -1)
    await settle()
    masks = [e[0] for _, e in server.pointer_events]
    assert masks == [0, 1, 0, 1, 0, 0, 16, 0, 16, 0, 0, 8, 0]


async def test_key_event_bytes(server: FakeRfbServer, client: VncClient) -> None:
    await client.key("ctrl+a")
    await settle()
    assert server.key_events == [
        struct.pack(">BxxI", 1, 0xFFE3),
        struct.pack(">BxxI", 1, 0x61),
        struct.pack(">BxxI", 0, 0x61),
        struct.pack(">BxxI", 0, 0xFFE3),
    ]


async def test_type_text_sends_keysyms(server: FakeRfbServer, client: VncClient) -> None:
    await client.type_text("á\n\t€")
    await settle()
    downs = [struct.unpack(">BxxI", e)[1] for e in server.key_events if e[0] == 1]
    assert downs == [0xE1, 0xFF0D, 0xFF09, 0x010020AC]
    assert len(server.key_events) == 8


async def test_raw_update_paints_framebuffer(server: FakeRfbServer, client: VncClient) -> None:
    await server.send_rect(10, 8, 4, 3, (200, 100, 50))
    image = await client.screenshot()
    assert image.size == (64, 48)
    assert image.getpixel((10, 8)) == (200, 100, 50)
    assert image.getpixel((13, 10)) == (200, 100, 50)
    assert image.getpixel((14, 10)) == (0, 0, 0)
    assert image.getpixel((9, 8)) == (0, 0, 0)


async def test_incremental_request_follows_update(server: FakeRfbServer, client: VncClient) -> None:
    await server.send_rect(0, 0, 2, 2, (1, 2, 3))
    await client.wait_stable()
    assert any(r[0] == 1 for r in server.update_requests)


async def test_desktop_size_resizes(server: FakeRfbServer, client: VncClient) -> None:
    await server.send_raw(struct.pack(">BxH", 0, 1) + struct.pack(">HHHHi", 0, 0, 80, 60, -223))
    image = await client.screenshot()
    assert image.size == (80, 60)


async def test_unknown_message_is_an_error(server: FakeRfbServer, client: VncClient) -> None:
    await server.send_raw(bytes([99]))
    await settle()
    with pytest.raises(VncError, match="unknown server message type 99"):
        await client.screenshot()


async def test_unknown_encoding_is_an_error(server: FakeRfbServer, client: VncClient) -> None:
    await server.send_raw(struct.pack(">BxH", 0, 1) + struct.pack(">HHHHi", 0, 0, 1, 1, 7))
    await settle()
    with pytest.raises(VncError, match="unsupported encoding 7"):
        await client.screenshot()


async def test_bell_cuttext_colourmap_are_consumed(
    server: FakeRfbServer, client: VncClient
) -> None:
    await server.send_raw(bytes([2]))
    await server.send_raw(struct.pack(">B3xI", 3, 5) + b"hello")
    await server.send_raw(struct.pack(">BxHH", 1, 0, 1) + bytes(6))
    await server.send_rect(0, 0, 1, 1, (9, 9, 9))
    image = await client.screenshot()
    assert image.getpixel((0, 0)) == (9, 9, 9)


async def test_non_ascii_chars_get_a_longer_pause(server: FakeRfbServer) -> None:
    timings = VncTimings(key_delay_s=0.001, non_ascii_delay_s=0.1, stable_quiet_s=0.1)
    async with VncClient(timings) as c:
        await c.connect(server.url, PASSWORD)
        await c.type_text("aáb")
    await settle()
    # key_times holds press/release per char: a↓ a↑ á↓ á↑ b↓ b↑
    gap_after_ascii = server.key_times[2] - server.key_times[1]
    gap_after_accent = server.key_times[4] - server.key_times[3]
    assert gap_after_accent >= 0.09
    assert gap_after_ascii < gap_after_accent


async def test_release_all_works_with_revoked_lease(server: FakeRfbServer) -> None:
    from aprendiz.vnc import Lease

    lease = Lease()
    async with VncClient(FAST, lease=lease) as c:
        await c.connect(server.url, PASSWORD)
        await c._pointer(3, 4, 1)
        await c._key_event(0x61, True)
        lease.revoke()
        with pytest.raises(VncError, match="lease revoked"):
            await c.click(1, 1)
        with pytest.raises(VncError, match="lease revoked"):
            await c.key("enter")
        await c.release_all()
        await c.release_all()  # idempotent: nothing left to release
        await settle()
    assert [e for _, e in server.pointer_events] == [
        bytes([1]) + struct.pack(">HH", 3, 4),
        bytes([0]) + struct.pack(">HH", 3, 4),
    ]
    assert server.key_events == [struct.pack(">BxxI", 1, 0x61), struct.pack(">BxxI", 0, 0x61)]
