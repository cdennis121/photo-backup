"""Turns a user pattern such as ``{year}/{month:02} - {month_name}/{filename}``
into a destination path for a single media file."""
from __future__ import annotations

import calendar
import re
import string
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePath
from typing import Callable, Iterator

TOKENS: dict[str, str] = {
    "year": "Four-digit year, e.g. 2026",
    "month": "Month number; use {month:02} for 09",
    "month_name": "Full month name, e.g. September",
    "month_short": "Short month name, e.g. Sep",
    "day": "Day of month; use {day:02} for 05",
    "hour": "Hour (24h); use {hour:02}",
    "minute": "Minute; use {minute:02}",
    "second": "Second; use {second:02}",
    "device": "Device name, e.g. My iPhone",
    "type": "Photos or Videos",
    "camera": "Camera model from metadata, or Unknown Camera",
    "filename": "Original file name with extension, e.g. IMG_0001.HEIC",
    "stem": "Original file name without extension",
    "ext": "Lower-case extension without the dot, e.g. heic",
}

_INVALID_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_MAX_COMPONENT = 200


@dataclass(frozen=True)
class PathContext:
    taken: datetime
    filename: str
    device: str
    media_type: str  # "photo" or "video"
    camera: str = ""

    def values(self) -> dict[str, object]:
        stem, ext = _split_ext(self.filename)
        return {
            "year": self.taken.year,
            "month": self.taken.month,
            "month_name": calendar.month_name[self.taken.month],
            "month_short": calendar.month_abbr[self.taken.month],
            "day": self.taken.day,
            "hour": self.taken.hour,
            "minute": self.taken.minute,
            "second": self.taken.second,
            "device": _clean_value(self.device) or "Unknown Device",
            "type": "Videos" if self.media_type == "video" else "Photos",
            "camera": _clean_value(self.camera) or "Unknown Camera",
            "filename": _clean_value(self.filename),
            "stem": _clean_value(stem),
            "ext": ext.lower(),
        }


class PatternError(ValueError):
    pass


def _split_ext(filename: str) -> tuple[str, str]:
    stem, dot, ext = filename.rpartition(".")
    if not dot or not stem:
        return filename, ""
    return stem, ext


def _clean_value(value: str) -> str:
    return value.replace("/", "-").replace("\\", "-").strip()


def pattern_fields(pattern: str) -> list[str]:
    try:
        return [f for _, f, _, _ in string.Formatter().parse(pattern) if f is not None]
    except ValueError as exc:
        raise PatternError(f"Malformed pattern: {exc}") from exc


def validate(pattern: str) -> list[str]:
    """Return a list of human-readable problems; empty means the pattern is usable."""
    if not pattern.strip():
        return ["Pattern is empty."]
    try:
        fields = pattern_fields(pattern)
    except PatternError as exc:
        return [str(exc)]
    problems = []
    unknown = sorted({f for f in fields if f.split(".")[0].split("[")[0] not in TOKENS or f == ""})
    if unknown:
        problems.append("Unknown token(s): " + ", ".join("{%s}" % u for u in unknown))
    if "filename" not in fields and "stem" not in fields:
        problems.append("Pattern must include {filename} or {stem} so files get unique names.")
    if pattern.strip().startswith(("/", "\\")) or re.match(r"^[a-zA-Z]:", pattern.strip()):
        problems.append("Pattern must be relative to the destination folder.")
    if not problems:
        try:
            render(pattern, _sample_context())
        except (PatternError, ValueError, KeyError, IndexError) as exc:
            problems.append(f"Pattern cannot be rendered: {exc}")
    return problems


def _sanitise_component(part: str) -> str:
    part = _INVALID_CHARS.sub("_", part).strip().rstrip(". ")
    if part.split(".")[0].upper() in _RESERVED:
        part = f"_{part}"
    if len(part) > _MAX_COMPONENT:
        stem, ext = _split_ext(part)
        keep = _MAX_COMPONENT - (len(ext) + 1 if ext else 0)
        part = f"{stem[:keep]}.{ext}" if ext else part[:_MAX_COMPONENT]
    return part


def render(pattern: str, ctx: PathContext) -> PurePath:
    """Render a pattern to a relative path. Always ends with the original extension."""
    values = ctx.values()
    fields = pattern_fields(pattern)
    try:
        text = pattern.format_map(values)
    except (KeyError, ValueError, IndexError) as exc:
        raise PatternError(str(exc)) from exc
    parts = [_sanitise_component(p) for p in re.split(r"[\\/]+", text)]
    parts = [p for p in parts if p and p not in (".", "..")]
    if not parts:
        parts = [values["filename"]]
    _, ext = _split_ext(ctx.filename)
    if ext and "filename" not in fields and "ext" not in fields:
        parts[-1] = f"{parts[-1]}.{ext}"
    return PurePath(*parts)


def build_path(root: Path, pattern: str, ctx: PathContext) -> Path:
    return Path(root) / render(pattern, ctx)


def candidate_paths(path: Path) -> Iterator[Path]:
    """``path``, then ``name_1.ext``, ``name_2.ext``... forever."""
    yield path
    stem, ext = _split_ext(path.name)
    counter = 1
    while True:
        yield path.with_name(f"{stem}_{counter}.{ext}" if ext else f"{stem}_{counter}")
        counter += 1


def unique_path(path: Path, exists: Callable[[Path], bool] = Path.exists) -> Path:
    """Return ``path`` or the first free ``name_1.ext``, ``name_2.ext``..."""
    return next(p for p in candidate_paths(path) if not exists(p))


def _sample_context(media_type: str = "photo") -> PathContext:
    return PathContext(
        taken=datetime(2026, 9, 5, 14, 30, 12),
        filename="IMG_0001.HEIC" if media_type == "photo" else "IMG_0002.MOV",
        device="My iPhone",
        media_type=media_type,
        camera="iPhone 17 Pro",
    )


def preview(pattern: str) -> list[str]:
    """Example rendered paths (photo and video) for the settings dialog."""
    return [str(render(pattern, _sample_context(t))) for t in ("photo", "video")]
