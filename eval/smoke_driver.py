"""Read-only smoke test: connect to the real desktop and save one screenshot."""

import asyncio
import os
import sys
from pathlib import Path

from aprendiz.vnc import VncClient

OUTPUT = Path("traces/smoke.png")


async def main() -> int:
    url = os.environ.get("DESKTOP_URL")
    password = os.environ.get("DESKTOP_PASSWORD")
    if not url or not password:
        print("DESKTOP_URL and DESKTOP_PASSWORD must be set", file=sys.stderr)
        return 2
    async with VncClient() as client:
        await client.connect(url, password)
        image = await client.screenshot()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUTPUT)
    print(f"saved {OUTPUT} ({image.width}x{image.height})")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
