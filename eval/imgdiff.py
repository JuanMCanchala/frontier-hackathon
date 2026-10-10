"""Image comparison with PIL only: dHash distance and changed boxes on a coarse grid."""

from collections import deque
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageDraw

Box = tuple[int, int, int, int]  # left, top, right, bottom (exclusive)


@dataclass(frozen=True)
class Thresholds:
    hash_distance: int = 6
    changed_area_px: int = 4096
    min_box_px: int = 1024  # boxes with a smaller area (a toast, a text cursor) are ignored
    cell_px: int = 16
    cell_mean_diff: float = 8.0


@dataclass(frozen=True)
class Comparison:
    distance: int
    boxes: list[Box]
    area: int
    changed: bool


def dhash(image: Image.Image) -> int:
    """64-bit difference hash: bit = pixel brighter than its right neighbour.

    BOX resampling (no ringing) keeps flat areas exactly equal, so tiny blips stay quiet.
    """
    small = image.convert("L").resize((9, 8), Image.Resampling.BOX)
    pixels = small.tobytes()
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (pixels[row * 9 + col] > pixels[row * 9 + col + 1])
    return bits


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def changed_boxes(a: Image.Image, b: Image.Image, t: Thresholds) -> list[Box]:
    """Rectangles around changed grid cells; adjacent cells (8-neighbourhood) are merged."""
    if a.size != b.size:
        return [(0, 0, max(a.width, b.width), max(a.height, b.height))]
    width, height = a.size
    cols, rows = -(-width // t.cell_px), -(-height // t.cell_px)
    diff = ImageChops.difference(a.convert("L"), b.convert("L"))
    padded = Image.new("L", (cols * t.cell_px, rows * t.cell_px))
    padded.paste(diff, (0, 0))
    means = padded.resize((cols, rows), Image.Resampling.BOX)
    hot = {
        (c, r)
        for r in range(rows)
        for c in range(cols)
        if means.getpixel((c, r)) > t.cell_mean_diff  # type: ignore[operator]
    }
    boxes: list[Box] = []
    while hot:
        queue = deque([hot.pop()])
        cells = list(queue)
        while queue:
            c, r = queue.popleft()
            for dc in (-1, 0, 1):
                for dr in (-1, 0, 1):
                    n = (c + dc, r + dr)
                    if n in hot:
                        hot.remove(n)
                        queue.append(n)
                        cells.append(n)
        xs, ys = [c for c, _ in cells], [r for _, r in cells]
        boxes.append(
            (
                min(xs) * t.cell_px,
                min(ys) * t.cell_px,
                min((max(xs) + 1) * t.cell_px, width),
                min((max(ys) + 1) * t.cell_px, height),
            )
        )
    return sorted(boxes)


def compare(reference: Image.Image, current: Image.Image, t: Thresholds) -> Comparison:
    distance = hamming(dhash(reference), dhash(current))
    boxes = [
        box
        for box in changed_boxes(reference, current, t)
        if (box[2] - box[0]) * (box[3] - box[1]) >= t.min_box_px
    ]
    area = sum((r - le) * (b - tp) for le, tp, r, b in boxes)
    return Comparison(distance, boxes, area, distance > t.hash_distance or area > t.changed_area_px)


def draw_boxes(image: Image.Image, boxes: list[Box]) -> Image.Image:
    marked = image.convert("RGB")
    draw = ImageDraw.Draw(marked)
    for box in boxes:
        draw.rectangle((box[0], box[1], box[2] - 1, box[3] - 1), outline=(255, 0, 0), width=3)
    return marked
