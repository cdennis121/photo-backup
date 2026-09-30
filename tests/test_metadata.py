from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from photobackup.core import metadata
from photobackup.core.metadata import MediaMetadata


def _jpeg_with_exif(path: Path, taken: str | None, make: str = "Google", model: str = "Pixel 9") -> Path:
    img = Image.new("RGB", (8, 8), "red")
    exif = img.getexif()
    exif[271] = make
    exif[272] = model
    if taken:
        exif.get_ifd(0x8769)[36867] = taken
    img.save(path, "JPEG", exif=exif)
    return path


def test_exif_date_and_camera(tmp_path: Path):
    meta = metadata.read_metadata(_jpeg_with_exif(tmp_path / "a.jpg", "2024:07:14 10:11:12"), "photo")
    assert meta.taken == datetime(2024, 7, 14, 10, 11, 12)
    assert meta.camera == "Google Pixel 9"


def test_camera_model_already_contains_make(tmp_path: Path):
    meta = metadata.read_metadata(_jpeg_with_exif(tmp_path / "a.jpg", None, "Apple", "Apple iPhone"), "photo")
    assert meta.camera == "Apple iPhone"


def test_heic_date(tmp_path: Path):
    import pillow_heif

    img = Image.new("RGB", (16, 16), "blue")
    exif = img.getexif()
    exif.get_ifd(0x8769)[36867] = "2025:12:24 18:00:00"
    path = tmp_path / "IMG_0001.HEIC"
    pillow_heif.from_pillow(img).save(path, exif=exif.tobytes())
    assert metadata.read_metadata(path, "photo").taken == datetime(2025, 12, 24, 18, 0, 0)


def test_photo_without_exif(tmp_path: Path):
    path = tmp_path / "plain.png"
    Image.new("RGB", (4, 4)).save(path)
    assert metadata.read_metadata(path, "photo") == MediaMetadata()


def test_corrupt_files_do_not_raise(tmp_path: Path):
    path = tmp_path / "broken.jpg"
    path.write_bytes(b"not an image")
    assert metadata.read_metadata(path, "photo") == MediaMetadata()
    video = tmp_path / "broken.mp4"
    video.write_bytes(b"not a video")
    assert metadata.read_metadata(video, "video").taken is None


def test_parse_exif_datetime_rejects_junk():
    assert metadata.parse_exif_datetime("0000:00:00 00:00:00") is None
    assert metadata.parse_exif_datetime("1970:01:01 00:00:00") is None
    assert metadata.parse_exif_datetime(None) is None


def test_parse_video_datetime_formats():
    local = datetime(2026, 9, 5, 14, 30, 12, tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
    assert metadata.parse_video_datetime("UTC 2026-09-05 14:30:12") == local
    assert metadata.parse_video_datetime("2026-09-05 14:30:12 UTC") == local
    assert metadata.parse_video_datetime("2026-09-05T15:30:12+0100") == datetime(2026, 9, 5, 15, 30, 12)
    assert metadata.parse_video_datetime("garbage") is None


def test_resolve_taken_fallback_order():
    embedded = datetime(2020, 1, 1)
    device = datetime(2021, 1, 1)
    fallback = datetime(2022, 1, 1)
    assert metadata.resolve_taken(MediaMetadata(taken=embedded), device, fallback) == embedded
    assert metadata.resolve_taken(MediaMetadata(), device, fallback) == device
    assert metadata.resolve_taken(MediaMetadata(), None, fallback) == fallback
    assert metadata.resolve_taken(MediaMetadata(), datetime(1980, 1, 1), fallback) == fallback
