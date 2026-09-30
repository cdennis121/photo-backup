"""Scan and backup pipelines, plus the QThread wrappers the UI runs them on."""
from __future__ import annotations

import hashlib
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Callable, Mapping, Protocol, Sequence

from PySide6.QtCore import QThread, Signal

from photobackup.config import DUPLICATE_SKIP, Settings, index_db_path
from photobackup.core import metadata, organiser
from photobackup.core.index_db import ImportedSet, IndexDB
from photobackup.core.scanner import MediaItem, MediaScanner, live_photo_pairs

log = logging.getLogger(__name__)

STAGING_DIR_NAME = ".photobackup-staging"
_MAX_CONSECUTIVE_FAILURES = 5


class MediaSource(Protocol):
    def copy_to(
        self,
        object_id: str,
        out: BinaryIO,
        on_chunk: Callable[[bytes], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> int: ...


class Cancelled(Exception):
    pass


@dataclass
class ScanResult:
    device_id: str
    items: list[MediaItem]
    new_items: list[MediaItem]
    live_pairs: dict[str, MediaItem] = field(default_factory=dict)  # video object id -> still

    @property
    def new_live_photos(self) -> int:
        new_ids = {i.object_id for i in self.new_items}
        return sum(1 for video_id in self.live_pairs if video_id in new_ids)

    @property
    def already_backed_up(self) -> int:
        return len(self.items) - len(self.new_items)

    def count(self, media_type: str, new_only: bool = True) -> int:
        return sum(1 for i in (self.new_items if new_only else self.items) if i.media_type == media_type)

    @property
    def new_bytes(self) -> int:
        return sum(i.size for i in self.new_items)


@dataclass
class BackupSummary:
    copied: int = 0
    skipped_existing: int = 0
    skipped_duplicate: int = 0
    failed: int = 0
    bytes_copied: int = 0
    cancelled: bool = False
    aborted_reason: str = ""
    errors: list[str] = field(default_factory=list)
    destination: str = ""


def classify_new(items: Sequence[MediaItem], imported: ImportedSet) -> list[MediaItem]:
    return [i for i in items if i not in imported]


def order_with_pairs(items: Sequence[MediaItem], live_pairs: Mapping[str, MediaItem]) -> list[MediaItem]:
    """Move each Live Photo video directly after its still so the still is placed first."""
    by_id = {i.object_id: i for i in items}
    video_for_still = {still.object_id: vid for vid, still in live_pairs.items() if vid in by_id and still.object_id in by_id}
    deferred = set(video_for_still.values())
    ordered = []
    for item in items:
        if item.object_id in deferred:
            continue
        ordered.append(item)
        if item.object_id in video_for_still:
            ordered.append(by_id[video_for_still[item.object_id]])
    return ordered


@dataclass(frozen=True)
class _Placement:
    taken: datetime | None
    camera: str
    dest: Path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class BackupEngine:
    """Copies items from a ``MediaSource`` into the organised destination tree."""

    def __init__(
        self,
        source: MediaSource,
        index: IndexDB,
        *,
        device_id: str,
        device_name: str,
        destination: Path,
        pattern: str,
        duplicate_policy: str = DUPLICATE_SKIP,
        should_cancel: Callable[[], bool] = lambda: False,
        on_file: Callable[[int, int, MediaItem], None] = lambda index, total, item: None,
        on_bytes: Callable[[int], None] = lambda n: None,
        on_log: Callable[[str], None] = lambda msg: None,
    ) -> None:
        self._source = source
        self._index = index
        self._device_id = device_id
        self._device_name = device_name
        self._root = Path(destination)
        self._pattern = pattern
        self._policy = duplicate_policy
        self._cancel = should_cancel
        self._on_file = on_file
        self._on_bytes = on_bytes
        self._log = on_log
        self._staging = self._root / STAGING_DIR_NAME

    def run(self, items: Sequence[MediaItem], live_pairs: Mapping[str, MediaItem] | None = None) -> BackupSummary:
        summary = BackupSummary(destination=str(self._root))
        problems = organiser.validate(self._pattern)
        if problems:
            summary.aborted_reason = "; ".join(problems)
            return summary
        self._pairs = dict(live_pairs or {})
        self._imported = self._index.imported_set(self._device_id)
        self._placed: dict[str, _Placement] = {}
        items = order_with_pairs(items, self._pairs)
        self._staging.mkdir(parents=True, exist_ok=True)
        _hide(self._staging)
        consecutive_failures = 0
        try:
            for position, item in enumerate(items, start=1):
                if self._cancel():
                    summary.cancelled = True
                    break
                self._on_file(position, len(items), item)
                try:
                    outcome = self._backup_one(item, summary)
                    consecutive_failures = 0
                except Cancelled:
                    summary.cancelled = True
                    break
                except Exception as exc:  # noqa: BLE001 - record and continue with the next file
                    log.exception("Failed to back up %s", item.name)
                    reason = _friendly_error(exc)
                    summary.failed += 1
                    summary.errors.append(f"{item.folder}/{item.name}: {reason}")
                    self._log(f"FAILED  {item.name}: {reason}")
                    consecutive_failures += 1
                    if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                        summary.aborted_reason = (
                            f"Stopped after {consecutive_failures} failures in a row. "
                            "The device may have been disconnected or locked."
                        )
                        break
                    continue
                self._log(outcome)
        finally:
            _remove_if_empty(self._staging)
        return summary

    def _backup_one(self, item: MediaItem, summary: BackupSummary) -> str:
        existing_record = self._imported.find(item)
        if existing_record:
            self._remember(item, existing_record.taken_at, "", Path(existing_record.dest_path))
            summary.skipped_existing += 1
            return f"skip    {item.name} (already backed up)"

        ext = item.name.rpartition(".")[2] if "." in item.name else "bin"
        temp = self._staging / f"{uuid.uuid4().hex}.{ext}.part"
        digest = hashlib.sha256()
        try:
            with open(temp, "wb") as out:
                written = self._source.copy_to(
                    item.object_id,
                    out,
                    on_chunk=lambda chunk: (digest.update(chunk), self._on_bytes(len(chunk))),
                    should_cancel=self._cancel,
                )
        except InterruptedError as exc:
            temp.unlink(missing_ok=True)
            raise Cancelled() from exc
        except BaseException:
            temp.unlink(missing_ok=True)
            raise

        if written == 0:
            temp.unlink(missing_ok=True)
            raise IOError("device returned no data")
        if written != item.size:
            log.warning("%s: device reported %d bytes, received %d", item.name, item.size, written)

        sha = digest.hexdigest()
        meta = metadata.read_metadata(temp, item.media_type)
        partner = self._partner_of(item)
        taken = (partner.taken if partner else None) or metadata.resolve_taken(meta, item.device_date)
        camera = meta.camera or (partner.camera if partner else "")
        live_note = " (Live Photo)" if partner else ""

        # "Keep both" is about copies across phones; the same phone's content is never stored twice.
        existing = self._index.find_by_hash(sha, device_id=None if self._policy == DUPLICATE_SKIP else self._device_id)
        if existing:
            temp.unlink(missing_ok=True)
            self._record(item, sha, Path(existing), taken)
            self._remember(item, taken, camera, Path(existing))
            summary.skipped_duplicate += 1
            return f"dupe    {item.name} (same as {existing})"

        ctx = organiser.PathContext(
            taken=taken, filename=item.name, device=self._device_name, media_type=item.media_type, camera=camera
        )
        target = organiser.build_path(self._root, self._pattern, ctx)
        if partner and target.parent == partner.dest.parent and target.stem != partner.dest.stem:
            target = target.with_name(partner.dest.stem + target.suffix)
        target, identical = self._free_target(target, written, sha)
        if identical:
            temp.unlink(missing_ok=True)
            self._record(item, sha, target, taken)
            self._remember(item, taken, camera, target)
            summary.skipped_duplicate += 1
            return f"dupe    {item.name} (already at {target})"

        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temp, target)
        stamp = taken.timestamp()
        os.utime(target, (stamp, stamp))
        self._record(item, sha, target, taken)
        self._remember(item, taken, camera, target)
        summary.copied += 1
        summary.bytes_copied += written
        return f"copied  {item.name} -> {target.relative_to(self._root)}{live_note}"

    def _remember(self, item: MediaItem, taken: datetime | None, camera: str, dest: Path) -> None:
        if item.media_type == "photo":
            self._placed[item.object_id] = _Placement(taken, camera, dest)

    def _partner_of(self, item: MediaItem) -> _Placement | None:
        """Where this Live Photo video's still went, from this run or an earlier backup."""
        still = self._pairs.get(item.object_id)
        if still is None:
            return None
        if still.object_id in self._placed:
            return self._placed[still.object_id]
        record = self._imported.find(still)
        return _Placement(record.taken_at, "", Path(record.dest_path)) if record else None

    def _free_target(self, target: Path, size: int, sha: str) -> tuple[Path, bool]:
        """First non-clashing path; ``identical`` is True if the file is already there."""
        for candidate in organiser.candidate_paths(target):
            if not candidate.exists():
                return candidate, False
            if self._policy == DUPLICATE_SKIP and candidate.stat().st_size == size and _sha256_file(candidate) == sha:
                return candidate, True
        raise AssertionError("unreachable")

    def _record(self, item: MediaItem, sha: str, dest: Path, taken: datetime | None) -> None:
        record = self._index.record(
            device_id=self._device_id,
            object_name=item.name,
            size=item.size,
            sha256=sha,
            dest_path=dest,
            taken_at=taken,
            persistent_id=item.persistent_id,
            device_date=item.device_date,
        )
        self._imported.add(record)


def _hide(path: Path) -> None:
    try:
        import ctypes

        ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x02)
    except (AttributeError, OSError):
        pass


def _remove_if_empty(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        pass


class ScanWorker(QThread):
    progress = Signal(int, str)  # items found so far, current folder
    finished_ok = Signal(object)  # ScanResult
    failed = Signal(str)

    def __init__(self, pnp_id: str, device_id: str, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self._pnp_id = pnp_id
        self._device_id = device_id
        self._settings = settings

    def run(self) -> None:
        from photobackup.devices import wpd

        try:
            with wpd.com_thread(), wpd.Device(self._pnp_id) as device:
                scanner = MediaScanner(
                    device,
                    include_photos=self._settings.include_photos,
                    include_videos=self._settings.include_videos,
                    scan_all_folders=self._settings.scan_all_folders,
                    should_cancel=self.isInterruptionRequested,
                    on_progress=self.progress.emit,
                )
                items = scanner.scan()
            with IndexDB(index_db_path()) as index:
                imported = index.imported_set(self._device_id)
            items.sort(key=lambda i: (i.device_date or datetime.min, i.name))
            self.finished_ok.emit(
                ScanResult(self._device_id, items, classify_new(items, imported), live_photo_pairs(items))
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("Scan failed")
            self.failed.emit(_friendly_error(exc))


class BackupWorker(QThread):
    file_started = Signal(int, int, str)  # position, total, name
    bytes_copied = Signal(int)
    log_line = Signal(str)
    finished_ok = Signal(object)  # BackupSummary
    failed = Signal(str)

    def __init__(
        self,
        pnp_id: str,
        device_id: str,
        device_name: str,
        items: Sequence[MediaItem],
        settings: Settings,
        live_pairs: Mapping[str, MediaItem] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._pnp_id = pnp_id
        self._device_id = device_id
        self._device_name = device_name
        self._items = list(items)
        self._settings = settings
        self._live_pairs = dict(live_pairs or {})

    def run(self) -> None:
        from photobackup.devices import wpd

        try:
            with wpd.com_thread(), wpd.Device(self._pnp_id) as device, IndexDB(index_db_path()) as index:
                engine = BackupEngine(
                    device,
                    index,
                    device_id=self._device_id,
                    device_name=self._device_name,
                    destination=Path(self._settings.destination),
                    pattern=self._settings.pattern,
                    duplicate_policy=self._settings.duplicate_policy,
                    should_cancel=self.isInterruptionRequested,
                    on_file=lambda pos, total, item: self.file_started.emit(pos, total, item.name),
                    on_bytes=self.bytes_copied.emit,
                    on_log=self.log_line.emit,
                )
                self.finished_ok.emit(engine.run(self._items, self._live_pairs))
        except Exception as exc:  # noqa: BLE001
            log.exception("Backup failed")
            self.failed.emit(_friendly_error(exc))


def _friendly_error(exc: Exception) -> str:
    from photobackup.devices.wpd import format_error

    text = format_error(exc)
    if "0x80070005" in text or "-2147024891" in text or "Access is denied" in text:
        return "Access denied by the device. Unlock it (and tap 'Trust' on an iPhone), then try again."
    if "0x8007001F" in text.upper() or "-2147024865" in text or "not functioning" in text:
        return "The device stopped responding. Reconnect it and try again."
    return text
