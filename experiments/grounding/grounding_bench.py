# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27"]
# ///
"""Grounding benchmark on a real SIGECOM screenshot: accuracy, latency and cost per model.

Usage: OPENROUTER_API_KEY=... uv run grounding_bench.py <screenshot.png> <runs> <model> [model...]
"""
import asyncio
import base64
import json
import os
import re
import sys
import time

import httpx

# Ground-truth boxes (x0, y0, x1, y1) measured on the 1280x800 screenshot.
TARGETS = {
    "menu_archivo": (14, 38, 70, 60),
    "menu_cartera": (176, 38, 232, 60),
    "menu_herramientas": (248, 38, 345, 60),
    "item_acerca_de": (354, 63, 562, 89),
    "texto_bienvenido": (14, 74, 220, 92),
}
TODAY = "14/10/2026"

PROMPT = f"""This is a 1280x800 screenshot of a legacy desktop app.
Return ONLY a JSON object with the pixel coordinates (x, y) of the CENTER of each element,
in this image's pixel space (x from 0 to 1280, y from 0 to 800), plus the date shown as "Hoy".
Elements:
- menu_archivo: top menu item "Archivo"
- menu_cartera: top menu item "Cartera"
- menu_herramientas: top menu item "Herramientas"
- item_acerca_de: dropdown item "Acerca de"
- texto_bienvenido: heading "Bienvenido a SIGECOM"
Format: {{"menu_archivo": [x, y], "menu_cartera": [x, y], "menu_herramientas": [x, y], "item_acerca_de": [x, y], "texto_bienvenido": [x, y], "hoy": "dd/mm/yyyy"}}"""


def inside(pt, box) -> bool:
    x, y = pt
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def score(answer: dict) -> tuple[int, int, bool]:
    """Best of pixel space vs. 0-1000 normalized space, since some models ignore the instruction."""
    best = 0
    for sx, sy in ((1.0, 1.0), (1280 / 1000, 800 / 1000)):
        hits = 0
        for name, box in TARGETS.items():
            pt = answer.get(name)
            if isinstance(pt, list) and len(pt) == 2:
                hits += inside((pt[0] * sx, pt[1] * sy), box)
        best = max(best, hits)
    return best, len(TARGETS), str(answer.get("hoy", "")).strip() == TODAY


async def run(client: httpx.AsyncClient, model: str, image_b64: str) -> dict:
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": 600,
        "usage": {"include": True},
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
        ]}],
    }
    t0 = time.monotonic()
    try:
        r = await client.post("https://openrouter.ai/api/v1/chat/completions", json=body, timeout=120)
        data = r.json()
        latency = time.monotonic() - t0
        text = data["choices"][0]["message"]["content"] or ""
        match = re.search(r"\{.*\}", text, re.S)
        answer = json.loads(match.group(0)) if match else {}
        hits, total, date_ok = score(answer)
        cost = data.get("usage", {}).get("cost", 0.0)
        return {"model": model, "hits": hits, "total": total, "date": date_ok, "latency": latency, "cost": cost}
    except Exception as exc:  # report and keep benchmarking the rest
        return {"model": model, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}


async def main() -> None:
    image_b64 = base64.b64encode(open(sys.argv[1], "rb").read()).decode()
    runs, models = int(sys.argv[2]), sys.argv[3:]
    headers = {"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"}
    async with httpx.AsyncClient(headers=headers) as client:
        results = await asyncio.gather(*(run(client, m, image_b64) for m in models for _ in range(runs)))
    print(f"{'model':42} {'hits':>7} {'hoy':>5} {'lat(s)':>7} {'cost($)':>9}")
    for model in models:
        rows = [r for r in results if r["model"] == model]
        errors = [r["error"] for r in rows if "error" in r]
        ok = [r for r in rows if "error" not in r]
        if not ok:
            print(f"{model:42} ERROR {errors[0]}")
            continue
        hits = sum(r["hits"] for r in ok)
        total = sum(r["total"] for r in ok)
        dates = sum(r["date"] for r in ok)
        lat = sum(r["latency"] for r in ok) / len(ok)
        cost = sum(r["cost"] for r in ok) / len(ok)
        print(f"{model:42} {hits:>3}/{total:<3} {dates:>2}/{len(ok):<2} {lat:>7.1f} {cost:>9.5f}")


if __name__ == "__main__":
    asyncio.run(main())
