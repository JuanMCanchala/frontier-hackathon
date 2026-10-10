"""App identity from the first screenshot: copied header title plus perceptual hash."""

import json
from pathlib import Path

from eval.imgdiff import dhash
from PIL import Image, ImageDraw

from aprendiz.agent.identity import read_app_identity, screen_dhash
from aprendiz.agent.llm import LlmError
from aprendiz.agent.memory import AppIdentity, AppMemory
from tests.agent.fakes import FakeLlm


def marked(x: int) -> Image.Image:
    image = Image.new("RGB", (320, 200), (230, 230, 230))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 319, 24), fill=(40, 60, 120))
    draw.rectangle((x, 40, x + 30, 70), fill=(10, 10, 10))
    return image


def test_screen_dhash_matches_eval_on_the_same_image() -> None:
    image = marked(20)
    assert screen_dhash(image) == f"{dhash(image):016x}"
    assert screen_dhash(marked(200)) != screen_dhash(image)


async def test_title_is_copied_and_not_reformatted() -> None:
    raw = "  Facturas  1.2 "
    llm = FakeLlm([json.dumps({"texto": raw})])
    image = marked(20)
    identity = await read_app_identity(llm, image, model="m")
    assert identity is not None
    assert identity.title == raw
    assert identity.screen_hash == screen_dhash(image)
    assert llm.calls[0]["images"] == [image]


async def test_unreadable_title_turns_memory_off() -> None:
    image = marked(20)
    assert await read_app_identity(FakeLlm(['{"texto": null}']), image, model="m") is None
    assert await read_app_identity(FakeLlm(['{"texto": "   "}']), image, model="m") is None
    assert await read_app_identity(FakeLlm([LlmError("down")]), image, model="m") is None
    assert await read_app_identity(FakeLlm(["no", "no"]), image, model="m") is None


def test_same_title_and_different_hash_do_not_share_memory(tmp_path: Path) -> None:
    memory = AppMemory(tmp_path / "app_memory.sqlite3")
    left = marked(20)
    right = marked(200)
    title = "Facturas 1.2"
    memory.save_menu_route(
        AppIdentity(title=title, screen_hash=screen_dhash(left)),
        ("Clientes",),
    )
    other = memory.list_current(AppIdentity(title=title, screen_hash=screen_dhash(right)))
    assert other.routes == ()
    assert memory.list_current(AppIdentity(title=title, screen_hash=screen_dhash(left))).routes
