"""Find photos and videos on a connected device."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable

from photobackup.devices import wpd

log = logging.getLogger(__name__)

PHOTO_EXTENSIONS = frozenset(
    {"jpg", "jpeg", "heic", "heif", "png", "gif", "webp", "dng", "raw", "cr2", "nef", "arw", "tif", "tiff", "bmp"}
)
VIDEO_EXTENSIONS = frozenset({"mov", "mp4", "m4v", "3gp", "3g2", "avi", "mkv", "webm", "mts", "m2ts"})

MEDIA_ROOTS = ("dcim", "pictures", "movies", "camera", "screenshots")
_SKIP_FOLDERS = {"android", ".thumbnails", ".trash", ".trashed", "cache", "$recycle.bin"}


@dataclass(frozen=True)
class MediaItem:
    object_id: str
    name: str
    size: int
    media_type: str  # "photo" | "video"
    device_date: datetime | None
    persistent_id: str
    folder: str

    @property
    def key(self) -> tuple[str, int]:
        return self.name, self.size


def media_type_for(name: str, content_type: str = "") -> str | None:
    ext = name.rpartition(".")[2].lower() if "." in name else ""
    if ext in PHOTO_EXTENSIONS:
        return "photo"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if content_type == wpd.CONTENT_TYPE_IMAGE:
        return "photo"
    if content_type == wpd.CONTENT_TYPE_VIDEO:
        return "video"
    return None


_LIVE_PHOTO_STILLS = frozenset({"heic", "heif", "jpg", "jpeg"})
_LIVE_PHOTO_MOTION = "mov"


def _split_name(name: str) -> tuple[str, str]:
    stem, dot, ext = name.rpartition(".")
    return (stem.lower(), ext.lower()) if dot else (name.lower(), "")


def live_photo_pairs(items: Iterable[MediaItem]) -> dict[str, MediaItem]:
    """Map each Live Photo video's object id to its still (IMG_0001.MOV -> IMG_0001.HEIC in the same folder)."""
    items = list(items)
    stills: dict[tuple[str, str], MediaItem] = {}
    for item in items:
        stem, ext = _split_name(item.name)
        if item.media_type == "photo" and ext in _LIVE_PHOTO_STILLS:
            stills.setdefault((item.folder, stem), item)
    pairs = {}
    for item in items:
        stem, ext = _split_name(item.name)
        if item.media_type == "video" and ext == _LIVE_PHOTO_MOTION and (item.folder, stem) in stills:
            pairs[item.object_id] = stills[(item.folder, stem)]
    return pairs


def _is_skipped(name: str) -> bool:
    lowered = name.lower()
    return lowered in _SKIP_FOLDERS or lowered.startswith(".")


class MediaScanner:
    def __init__(
        self,
        device: wpd.Device,
        *,
        include_photos: bool = True,
        include_videos: bool = True,
        scan_all_folders: bool = False,
        should_cancel: Callable[[], bool] = lambda: False,
        on_progress: Callable[[int, str], None] = lambda count, folder: None,
    ) -> None:
        self._device = device
        self._types = {t for t, on in (("photo", include_photos), ("video", include_videos)) if on}
        self._scan_all = scan_all_folders
        self._cancel = should_cancel
        self._progress = on_progress
        self._found = 0

    def scan(self) -> list[MediaItem]:
        items: list[MediaItem] = []
        for storage in self._device.list_objects(wpd.WPD_DEVICE_OBJECT_ID):
            if self._cancel():
                break
            items.extend(self._scan_storage(storage))
        return items

    def _scan_storage(self, storage: wpd.WpdObject) -> Iterable[MediaItem]:
        top = list(self._device.list_objects(storage.object_id))
        roots = top if self._scan_all else [o for o in top if o.is_folder and o.name.lower() in MEDIA_ROOTS]
        if not roots:
            roots = top
        base = storage.name or "Storage"
        for obj in roots:
            if obj.is_folder:
                if not _is_skipped(obj.name):
                    yield from self._walk(obj, f"{base}/{obj.name}")
            else:
                item = self._to_item(obj, base)
                if item:
                    yield item

    def _walk(self, folder: wpd.WpdObject, path: str) -> Iterable[MediaItem]:
        stack = [(folder.object_id, path)]
        while stack:
            if self._cancel():
                return
            parent_id, parent_path = stack.pop()
            self._progress(self._found, parent_path)
            try:
                children = list(self._device.list_objects(parent_id))
            except Exception as exc:  # noqa: BLE001 - one unreadable folder should not stop the scan
                log.warning("Cannot list %s: %s", parent_path, exc)
                continue
            for obj in children:
                if obj.is_folder:
                    if not _is_skipped(obj.name):
                        stack.append((obj.object_id, f"{parent_path}/{obj.name}"))
                    continue
                item = self._to_item(obj, parent_path)
                if item:
                    yield item

    def _to_item(self, obj: wpd.WpdObject, folder: str) -> MediaItem | None:
        media_type = media_type_for(obj.name, obj.content_type)
        if media_type not in self._types or not obj.size:
            return None
        self._found += 1
        return MediaItem(
            object_id=obj.object_id,
            name=obj.name,
            size=obj.size,
            media_type=media_type,
            device_date=obj.date_created or obj.date_modified,
            persistent_id=obj.persistent_id,
            folder=folder,
        )
