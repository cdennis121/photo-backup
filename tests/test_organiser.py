from datetime import datetime
from pathlib import Path, PurePath

import pytest

from photobackup.core import organiser
from photobackup.core.organiser import PathContext

CTX = PathContext(
    taken=datetime(2026, 9, 5, 14, 3, 7),
    filename="IMG_0001.HEIC",
    device="Sam's iPhone",
    media_type="photo",
    camera="iPhone 17 Pro",
)


def test_default_pattern():
    assert organiser.render("{year}/{month:02} - {month_name}/{filename}", CTX) == PurePath(
        "2026", "09 - September", "IMG_0001.HEIC"
    )


def test_type_and_device_tokens():
    video = PathContext(taken=CTX.taken, filename="IMG_0002.MOV", device="Pixel 9", media_type="video")
    assert organiser.render("{type}/{device}/{filename}", video) == PurePath("Videos", "Pixel 9", "IMG_0002.MOV")
    assert organiser.render("{type}/{filename}", CTX) == PurePath("Photos", "IMG_0001.HEIC")


def test_stem_pattern_keeps_extension():
    path = organiser.render("{year}{month:02}{day:02}_{hour:02}{minute:02}{second:02}_{stem}", CTX)
    assert path == PurePath("20260905_140307_IMG_0001.HEIC")


def test_ext_token_suppresses_automatic_extension():
    assert organiser.render("{stem}.{ext}", CTX) == PurePath("IMG_0001.heic")


def test_missing_camera_uses_placeholder():
    ctx = PathContext(taken=CTX.taken, filename="a.jpg", device="", media_type="photo")
    assert organiser.render("{camera}/{device}/{filename}", ctx) == PurePath("Unknown Camera", "Unknown Device", "a.jpg")


def test_invalid_characters_and_traversal_are_sanitised():
    ctx = PathContext(taken=CTX.taken, filename="clip.mp4", device='Bad:Name?/..\\x', media_type="video")
    path = organiser.render("../{device}/./{filename}", ctx)
    assert ".." not in path.parts
    assert all(not set(part) & set('<>:"|?*') for part in path.parts)
    assert path.name == "clip.mp4"


def test_reserved_windows_names():
    ctx = PathContext(taken=CTX.taken, filename="CON.jpg", device="x", media_type="photo")
    assert organiser.render("{filename}", ctx).name == "_CON.jpg"


@pytest.mark.parametrize(
    "pattern, fragment",
    [
        ("", "empty"),
        ("{year}/{bogus}/{filename}", "Unknown token"),
        ("{year}/{month}", "must include"),
        ("{year/{filename}", "Malformed"),
        ("C:/{filename}", "relative"),
        ("{month:zz}/{filename}", "cannot be rendered"),
    ],
)
def test_validate_rejects_bad_patterns(pattern, fragment):
    problems = organiser.validate(pattern)
    assert problems and fragment.lower() in " ".join(problems).lower()


def test_validate_accepts_presets():
    from photobackup.ui.settings_dialog import PATTERN_PRESETS

    for pattern in PATTERN_PRESETS:
        assert organiser.validate(pattern) == [], pattern


def test_unique_path(tmp_path: Path):
    (tmp_path / "a.jpg").write_bytes(b"1")
    (tmp_path / "a_1.jpg").write_bytes(b"2")
    assert organiser.unique_path(tmp_path / "a.jpg") == tmp_path / "a_2.jpg"
    assert organiser.unique_path(tmp_path / "b.jpg") == tmp_path / "b.jpg"


def test_preview_returns_photo_and_video():
    photo, video = organiser.preview("{type}/{filename}")
    assert photo.startswith("Photos") and video.startswith("Videos")
