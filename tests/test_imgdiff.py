from eval.imgdiff import Thresholds, changed_boxes, compare, dhash, hamming
from PIL import Image, ImageDraw

T = Thresholds()


def page(*rects: tuple[int, int, int, int], color: int = 0) -> Image.Image:
    """A 320x200 synthetic screen: light grey with a dark header and optional rectangles."""
    img = Image.new("RGB", (320, 200), (230, 230, 230))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, 319, 30), fill=(40, 60, 120))
    for r in rects:
        draw.rectangle(r, fill=(color, color, color))
    return img


def test_identical_images_have_no_change() -> None:
    result = compare(page(), page(), T)
    assert (result.distance, result.boxes, result.area, result.changed) == (0, [], 0, False)


def test_new_large_rectangle_is_one_box_in_the_right_place() -> None:
    result = compare(page(), page((100, 80, 219, 159)), T)
    assert result.changed
    assert len(result.boxes) == 1
    left, top, right, bottom = result.boxes[0]
    assert (left, top) == (96, 80) and (right, bottom) == (224, 160)  # aligned to the 16 px grid
    assert result.area == (224 - 96) * (160 - 80)


def test_tiny_change_is_ignored() -> None:
    result = compare(page(), page((150, 100, 153, 112)), T)  # a text-cursor sized blip
    assert changed_boxes(page(), page((150, 100, 153, 112)), T) != []  # seen by the grid...
    assert result.boxes == [] and not result.changed  # ...but dropped by the size filter


def test_two_separate_changes_give_two_boxes() -> None:
    boxes = changed_boxes(page(), page((20, 60, 80, 100), (200, 120, 300, 180)), T)
    assert len(boxes) == 2


def test_dhash_distance_grows_with_different_layouts() -> None:
    a = (
        Image.linear_gradient("L")
        .transpose(Image.Transpose.ROTATE_90)
        .resize((320, 200))
        .convert("RGB")
    )
    assert hamming(dhash(a), dhash(a)) == 0
    assert hamming(dhash(a), dhash(a.transpose(Image.Transpose.FLIP_LEFT_RIGHT))) > 20


def test_size_change_is_a_full_box() -> None:
    assert changed_boxes(page(), Image.new("RGB", (100, 100)), T) == [(0, 0, 320, 200)]


# ---- alert dedupe -------------------------------------------------------------
