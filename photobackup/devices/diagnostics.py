"""Write a plain-text report of what a device exposes over WPD, for troubleshooting."""
from __future__ import annotations

import logging
import platform
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Protocol, TextIO

from PySide6.QtCore import QThread, Signal

from photobackup import __version__
from photobackup.core.scanner import MEDIA_ROOTS, media_type_for
from photobackup.devices import wpd

log = logging.getLogger(__name__)

_FOLDER_TYPES = {"Folder", "Storage"}
_LIVE_STILLS = {"heic", "heif", "jpg", "jpeg"}


class InspectableDevice(Protocol):
    def children(self, parent_id: str) -> Iterable[str]: ...
    def raw_properties(self, object_id: str) -> dict[str, str]: ...


@dataclass
class ReportStats:
    folders: int = 0
    files: int = 0
    total_bytes: int = 0
    not_inspected: int = 0
    photos: int = 0
    videos: int = 0
    live_pairs: int = 0
    missing_date: int = 0
    missing_persistent_id: int = 0
    extensions: Counter = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)


def _mask(serial: str) -> str:
    return f"{'*' * max(0, len(serial) - 4)}{serial[-4:]}" if serial else "(none)"


def _size(text: str) -> str:
    if not text:
        return "? B"
    n = float(text)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _ext(name: str) -> str:
    return name.rpartition(".")[2].lower() if "." in name else ""


class DiagnosticsReport:
    def __init__(
        self,
        info: wpd.DeviceInfo,
        device: InspectableDevice | None,
        *,
        open_error: str = "",
        max_depth: int = 8,
        max_listed_files: int = 25,
        max_inspected: int = 5000,
        log_path: Path | None = None,
        should_cancel: Callable[[], bool] = lambda: False,
        on_progress: Callable[[str], None] = lambda text: None,
    ) -> None:
        self._info = info
        self._device = device
        self._open_error = open_error
        self._max_depth = max_depth
        self._max_listed = max_listed_files
        self._max_inspected = max_inspected
        self._log_path = log_path
        self._cancel = should_cancel
        self._progress = on_progress
        self._inspected = 0
        self.stats = ReportStats()

    def write(self, out: TextIO) -> ReportStats:
        self._out = out
        self._header()
        if self._device is not None:
            self._line("-- Contents --")
            try:
                self._walk(wpd.WPD_DEVICE_OBJECT_ID, depth=0)
            except Exception as exc:  # noqa: BLE001 - the report should always be written
                self.stats.errors.append(f"Walking the device failed: {exc}")
            self._line()
        self._summary()
        self._log_tail()
        return self.stats

    def _line(self, text: str = "") -> None:
        self._out.write(text + "\n")

    def _header(self) -> None:
        i = self._info
        self._line("== PhotoBackup device diagnostics ==")
        self._line(f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}")
        self._line(f"App: {__version__}   Python: {sys.version.split()[0]}   Windows: {platform.platform()}")
        self._line("Note: this report lists folder and file names from the device. Review it before sharing.")
        self._line()
        self._line("-- Device --")
        self._line(f"Name:         {i.display_name}")
        self._line(f"Detected as:  {i.kind}")
        self._line(f"Manufacturer: {i.manufacturer or '(unknown)'}")
        self._line(f"Model:        {i.model or '(unknown)'}")
        self._line(f"Serial:       {_mask(i.serial)}")
        self._line(f"PnP ID:       {i.pnp_id}")
        self._line(f"Accessible:   {'yes' if i.accessible else 'no'}")
        if i.error or self._open_error:
            self._line(f"Open error:   {self._open_error or i.error}")
        self._line()

    def _walk(self, parent_id: str, depth: int) -> None:
        if self._cancel():
            raise InterruptedError("Cancelled")
        indent = "  " * depth
        try:
            child_ids = list(self._device.children(parent_id))
        except Exception as exc:  # noqa: BLE001
            self.stats.errors.append(f"Cannot list {parent_id}: {exc}")
            self._line(f"{indent}(cannot list: {exc})")
            return

        folders: list[tuple[str, dict[str, str]]] = []
        files: list[tuple[str, dict[str, str]]] = []
        for object_id in child_ids:
            if self._inspected >= self._max_inspected:
                self.stats.not_inspected += 1
                continue
            self._inspected += 1
            try:
                props = self._device.raw_properties(object_id)
            except Exception as exc:  # noqa: BLE001
                self.stats.errors.append(f"Cannot read {object_id}: {exc}")
                continue
            (folders if props.get("content_type") in _FOLDER_TYPES else files).append((object_id, props))

        if files:
            self._files(files, indent)
        for object_id, props in folders:
            self.stats.folders += 1
            name = props.get("original_name") or props.get("name") or "(unnamed)"
            note = ""
            if props.get("content_type") == "Storage":
                note = self._media_root_note(object_id)
            self._line(f"{indent}[{props.get('content_type')}] {name}  (id={object_id}){note}")
            self._progress(name)
            if depth + 1 >= self._max_depth:
                self._line(f"{indent}  (depth limit reached)")
                continue
            self._walk(object_id, depth + 1)

    def _media_root_note(self, storage_id: str) -> str:
        try:
            names = [self._device.raw_properties(c).get("name", "") for c in self._device.children(storage_id)]
        except Exception:  # noqa: BLE001
            return ""
        roots = [n for n in names if n.lower() in MEDIA_ROOTS]
        return f"  media folders: {', '.join(roots) if roots else 'none (whole storage will be scanned)'}"

    def _files(self, files: list[tuple[str, dict[str, str]]], indent: str) -> None:
        stills, movs = set(), set()
        media = 0
        for _, props in files:
            name = props.get("original_name") or props.get("name") or ""
            ext = _ext(name)
            kind = media_type_for(name, "")
            self.stats.files += 1
            self.stats.extensions[ext or "(none)"] += 1
            if props.get("size"):
                self.stats.total_bytes += int(props["size"])
            if kind == "photo":
                self.stats.photos += 1
            elif kind == "video":
                self.stats.videos += 1
            media += kind is not None
            if not (props.get("authored") or props.get("created") or props.get("modified")):
                self.stats.missing_date += 1
            if not props.get("persistent_id"):
                self.stats.missing_persistent_id += 1
            stem = name.rpartition(".")[0].lower()
            if ext in _LIVE_STILLS:
                stills.add(stem)
            elif ext == "mov":
                movs.add(stem)
        pairs = len(stills & movs)
        self.stats.live_pairs += pairs
        shown = min(len(files), self._max_listed)
        pair_note = f", {pairs} Live Photo pairs" if pairs else ""
        self._line(f"{indent}{len(files)} files ({media} photos/videos{pair_note}), showing {shown}:")
        for object_id, props in files[:shown]:
            name = props.get("original_name") or props.get("name") or "(unnamed)"
            renamed = f"  [name='{props.get('name')}']" if props.get("name") and props.get("name") != name else ""
            self._line(
                f"{indent}  {name}  | {props.get('content_type') or '?'} | {_size(props.get('size', ''))}"
                f" | created {props.get('created') or '-'} | modified {props.get('modified') or '-'}"
                f" | authored {props.get('authored') or '-'} | pid {props.get('persistent_id') or '-'}"
                f" | id {object_id}{renamed}"
            )

    def _summary(self) -> None:
        s = self.stats
        self._line("-- Summary --")
        self._line(f"Folders: {s.folders}   Files: {s.files}   Total size: {_size(str(s.total_bytes))}")
        self._line(f"Photos: {s.photos}   Videos: {s.videos}   Live Photo pairs: {s.live_pairs}")
        self._line(f"Files without any date: {s.missing_date}   Files without a persistent id: {s.missing_persistent_id}")
        if s.not_inspected:
            self._line(f"Objects not inspected (limit of {self._max_inspected} reached): {s.not_inspected}")
        top = ", ".join(f"{ext} {n}" for ext, n in s.extensions.most_common(15))
        self._line(f"Extensions: {top or '(none)'}")
        if s.errors:
            self._line(f"Errors ({len(s.errors)}):")
            for error in s.errors[:50]:
                self._line(f"  {error}")
        self._line()

    def _log_tail(self, lines: int = 80) -> None:
        if not self._log_path or not self._log_path.exists():
            return
        try:
            tail = self._log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
        except OSError:
            return
        self._line(f"-- Last {len(tail)} lines of {self._log_path.name} --")
        for text in tail:
            self._line(text)


class DiagnoseWorker(QThread):
    progress = Signal(str)
    finished_ok = Signal(str)  # report path
    failed = Signal(str)

    def __init__(self, pnp_id: str, report_path: Path, log_path: Path | None = None, parent=None) -> None:
        super().__init__(parent)
        self._pnp_id = pnp_id
        self._path = Path(report_path)
        self._log_path = log_path

    def run(self) -> None:
        try:
            with wpd.com_thread():
                info = wpd.DeviceManager().describe(self._pnp_id)
                device, open_error = None, ""
                try:
                    device = wpd.Device(self._pnp_id)
                except Exception as exc:  # noqa: BLE001
                    open_error = wpd.format_error(exc)
                try:
                    report = DiagnosticsReport(
                        info,
                        device,
                        open_error=open_error,
                        log_path=self._log_path,
                        should_cancel=self.isInterruptionRequested,
                        on_progress=self.progress.emit,
                    )
                    with open(self._path, "w", encoding="utf-8") as out:
                        report.write(out)
                finally:
                    if device is not None:
                        device.close()
            self.finished_ok.emit(str(self._path))
        except Exception as exc:  # noqa: BLE001
            log.exception("Diagnostics failed")
            self.failed.emit(str(exc))
