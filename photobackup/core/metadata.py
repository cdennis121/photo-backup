"""Extract the capture date and camera model from a local copy of a media file."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

try:
    from PIL import Image

    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except ImportError:  # pragma: no cover - optional
        log.warning("pillow-heif not installed; HEIC dates will use device timestamps")
except ImportError:  # pragma: no cover - optional
    Image = None

try:
    from pymediainfo import MediaInfo
except ImportError:  # pragma: no cover - optional
    MediaInfo = None

_EXIF_IFD = 0x8769
_TAG_DATETIME_ORIGINAL = 36867
_TAG_DATETIME_DIGITIZED = 36868
_TAG_DATETIME = 306
_TAG_MAKE = 271
_TAG_MODEL = 272

_EARLIEST_PLAUSIBLE = datetime(1990, 1, 1)


@dataclass(frozen=True)
class MediaMetadata:
    taken: datetime | None = None
    camera: str = ""


def _plausible(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value < _EARLIEST_PLAUSIBLE or value > datetime.now().replace(year=datetime.now().year + 1):
        return None
    return value


def parse_exif_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    value = value.strip().replace("\x00", "")
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M"):
        try:
            return _plausible(datetime.strptime(value[:19], fmt))
        except ValueError:
            continue
    return None


def parse_video_datetime(value: object) -> datetime | None:
    """Handle MediaInfo formats: 'UTC 2026-09-05 14:30:12', '2026-09-05 14:30:12 UTC',
    and Apple's local '2026-09-05T15:30:12+0100'."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    is_utc = "UTC" in text
    text = text.replace("UTC", "").strip()
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?\s*([+-]\d{2}:?\d{2}|Z)?", text)
    if not match:
        return None
    parts = [int(g) for g in match.groups()[:6]]
    offset = match.group(7)
    try:
        parsed = datetime(*parts)
    except ValueError:
        return None
    if offset:
        # Apple writes local wall-clock time plus offset; the wall-clock part is what users expect.
        return _plausible(parsed)
    if is_utc:
        return _plausible(parsed.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None))
    return _plausible(parsed)


def _photo_metadata(path: Path) -> MediaMetadata:
    if Image is None:
        return MediaMetadata()
    try:
        with Image.open(path) as img:
            exif = img.getexif()
    except Exception as exc:  # noqa: BLE001 - any decoder failure means "no metadata"
        log.debug("No EXIF for %s: %s", path, exc)
        return MediaMetadata()
    if not exif:
        return MediaMetadata()
    sub = exif.get_ifd(_EXIF_IFD) if hasattr(exif, "get_ifd") else {}
    taken = (
        parse_exif_datetime(sub.get(_TAG_DATETIME_ORIGINAL))
        or parse_exif_datetime(sub.get(_TAG_DATETIME_DIGITIZED))
        or parse_exif_datetime(exif.get(_TAG_DATETIME))
    )
    return MediaMetadata(taken=taken, camera=_camera(exif.get(_TAG_MAKE), exif.get(_TAG_MODEL)))


def _camera(make: object, model: object) -> str:
    make = str(make or "").strip().strip("\x00")
    model = str(model or "").strip().strip("\x00")
    if make and model and not model.lower().startswith(make.lower()):
        return f"{make} {model}"
    return model or make


def _video_metadata(path: Path) -> MediaMetadata:
    if MediaInfo is None:
        return MediaMetadata()
    try:
        info = MediaInfo.parse(str(path))
    except Exception as exc:  # noqa: BLE001
        log.debug("MediaInfo failed for %s: %s", path, exc)
        return MediaMetadata()
    general = next((t for t in info.tracks if t.track_type == "General"), None)
    if general is None:
        return MediaMetadata()
    data = general.to_data()
    taken = None
    for key in ("comapplequicktimecreationdate", "com_apple_quicktime_creationdate", "encoded_date", "tagged_date", "recorded_date"):
        taken = parse_video_datetime(data.get(key))
        if taken:
            break
    camera = _camera(
        data.get("comapplequicktimemake") or data.get("com_android_manufacturer"),
        data.get("comapplequicktimemodel") or data.get("com_android_model"),
    )
    return MediaMetadata(taken=taken, camera=camera)


def read_metadata(path: Path, media_type: str) -> MediaMetadata:
    if media_type == "video":
        return _video_metadata(path)
    return _photo_metadata(path)


def resolve_taken(meta: MediaMetadata, device_date: datetime | None, fallback: datetime | None = None) -> datetime:
    """Embedded metadata wins, then the device's object date, then ``fallback`` (or now)."""
    return meta.taken or _plausible(device_date) or fallback or datetime.now()
