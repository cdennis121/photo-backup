from __future__ import annotations

import os
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from photobackup import APP_NAME, PROJECT_URL, __version__
from photobackup.config import Settings, app_data_dir
from photobackup.core.backup_worker import BackupSummary, BackupWorker, ScanResult, ScanWorker
from photobackup.devices import wpd
from photobackup.devices.diagnostics import DiagnoseWorker
from photobackup.devices.watcher import DeviceWatcher
from photobackup.ui.settings_dialog import SettingsDialog

_KIND_LABEL = {wpd.KIND_IPHONE: "iPhone", wpd.KIND_ANDROID: "Android", wpd.KIND_OTHER: "Portable device"}


def _human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.setWindowTitle(APP_NAME)
        self.resize(980, 640)

        self._devices: dict[str, wpd.DeviceInfo] = {}
        self._scan: ScanResult | None = None
        self._scan_worker: ScanWorker | None = None
        self._backup_worker: BackupWorker | None = None
        self._backup_pnp: str | None = None
        self._diagnose_worker: DiagnoseWorker | None = None
        self._bytes_done = 0
        self._bytes_total = 0
        self._started_at = 0.0

        self._build_ui()
        self._build_menu()
        self._refresh_controls()

        self.watcher = DeviceWatcher(settings.poll_interval_seconds, self)
        self.watcher.device_added.connect(self._on_device_added)
        self.watcher.device_changed.connect(self._on_device_changed)
        self.watcher.device_removed.connect(self._on_device_removed)
        self.watcher.watcher_error.connect(lambda msg: self._log(f"Device watcher: {msg}"))
        self.watcher.start()
        self.statusBar().showMessage("Waiting for a phone to be connected...")

    # ---------- layout ----------

    def _build_ui(self) -> None:
        style = self.style()
        self._icon_phone = style.standardIcon(QStyle.SP_DriveCDIcon)
        self._icon_locked = style.standardIcon(QStyle.SP_MessageBoxWarning)

        self.device_list = QListWidget()
        self.device_list.setIconSize(self.device_list.iconSize() * 1.5)
        self.device_list.setWordWrap(True)
        self.device_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.device_list.setSpacing(2)
        self.device_list.currentItemChanged.connect(lambda *_: self._on_selection_changed())
        empty_hint = QLabel(
            "Connect an iPhone or Android phone with a USB cable.\n\n"
            "iPhone: unlock it and tap 'Trust'.\n"
            "Android: choose 'File transfer' in the USB notification."
        )
        empty_hint.setWordWrap(True)
        empty_hint.setStyleSheet("color: palette(mid);")
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("<b>Devices</b>"))
        left_layout.addWidget(self.device_list, 1)
        left_layout.addWidget(empty_hint)

        self.device_title = QLabel("No device selected")
        self.device_title.setStyleSheet("font-size: 16pt; font-weight: 600;")
        self.device_status = QLabel("")
        self.device_status.setWordWrap(True)
        self.scan_summary = QLabel("")
        self.scan_summary.setWordWrap(True)

        self.scan_button = QPushButton("Scan")
        self.scan_button.clicked.connect(self._start_scan)
        self.backup_button = QPushButton("Back up")
        self.backup_button.setDefault(True)
        self.backup_button.clicked.connect(self._start_backup)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self._cancel)
        self.diagnose_button = QPushButton("Save diagnostics...")
        self.diagnose_button.setToolTip("Save a text report of what the device exposes, for troubleshooting")
        self.diagnose_button.clicked.connect(self._start_diagnostics)
        buttons = QHBoxLayout()
        buttons.addWidget(self.scan_button)
        buttons.addWidget(self.backup_button)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch(1)
        buttons.addWidget(self.diagnose_button)

        device_box = QGroupBox("Device")
        device_layout = QVBoxLayout(device_box)
        device_layout.addWidget(self.device_title)
        device_layout.addWidget(self.device_status)
        device_layout.addWidget(self.scan_summary)
        device_layout.addLayout(buttons)

        self.destination = QLineEdit(self.settings.destination)
        self.destination.setReadOnly(True)
        self.destination.setFocusPolicy(Qt.ClickFocus)
        browse = QPushButton("Change...")
        browse.clicked.connect(self._choose_destination)
        self.open_button = QPushButton("Open folder")
        self.open_button.clicked.connect(self._open_destination)
        settings_button = QPushButton("Settings...")
        settings_button.clicked.connect(self._open_settings)
        dest_row = QHBoxLayout()
        dest_row.addWidget(self.destination, 1)
        dest_row.addWidget(browse)
        dest_row.addWidget(self.open_button)
        dest_row.addWidget(settings_button)
        self.pattern_label = QLabel()
        self.pattern_label.setStyleSheet("color: palette(mid);")
        dest_box = QGroupBox("Backup destination")
        dest_layout = QVBoxLayout(dest_box)
        dest_layout.addLayout(dest_row)
        dest_layout.addWidget(self.pattern_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress_label = QLabel("")
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        progress_box = QGroupBox("Progress")
        progress_layout = QVBoxLayout(progress_box)
        progress_layout.addWidget(self.progress)
        progress_layout.addWidget(self.progress_label)
        progress_layout.addWidget(self.log_view, 1)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(device_box)
        right_layout.addWidget(dest_box)
        right_layout.addWidget(progress_box, 1)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([280, 700])

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.addWidget(splitter)
        self.setCentralWidget(container)
        self._update_pattern_label()

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        settings_action = QAction("&Settings...", self)
        settings_action.triggered.connect(self._open_settings)
        file_menu.addAction(settings_action)
        self._diagnose_action = QAction("Save device &diagnostics...", self)
        self._diagnose_action.triggered.connect(self._start_diagnostics)
        file_menu.addAction(self._diagnose_action)
        file_menu.addSeparator()
        quit_action = QAction("E&xit", self)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)
        help_menu = self.menuBar().addMenu("&Help")
        about = QAction("&About", self)
        about.triggered.connect(self._show_about)
        help_menu.addAction(about)

    def _show_about(self) -> None:
        source = f'<br>Source code: <a href="{PROJECT_URL}">{PROJECT_URL}</a>' if PROJECT_URL else ""
        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            f"<b>{APP_NAME}</b> {__version__}<br>"
            "Back up and organise photos and videos from iPhone and Android devices.<br><br>"
            "This program is free software, licensed under the GNU General Public License v3.0 or later. "
            "It comes with ABSOLUTELY NO WARRANTY. See the LICENSE file for details."
            f"{source}",
        )

    # ---------- state helpers ----------

    def _workers(self) -> tuple:
        return (self._scan_worker, self._backup_worker, self._diagnose_worker)

    def _busy(self) -> bool:
        return any(w is not None and w.isRunning() for w in self._workers())

    def _selected(self) -> wpd.DeviceInfo | None:
        item = self.device_list.currentItem()
        return self._devices.get(item.data(Qt.UserRole)) if item else None

    def _refresh_controls(self) -> None:
        info = self._selected()
        busy = self._busy()
        ready = info is not None and info.accessible
        self.scan_button.setEnabled(ready and not busy)
        new_count = len(self._scan.new_items) if self._scan and info and self._scan.device_id == info.identity else 0
        self.backup_button.setEnabled(ready and not busy and new_count > 0)
        self.backup_button.setText(f"Back up {new_count} new file{'s' if new_count != 1 else ''}" if new_count else "Back up")
        self.cancel_button.setEnabled(busy)
        self.diagnose_button.setEnabled(info is not None and not busy)
        self._diagnose_action.setEnabled(info is not None and not busy)
        self.device_list.setEnabled(not busy)

    def _log(self, message: str) -> None:
        self.log_view.appendPlainText(f"{time.strftime('%H:%M:%S')}  {message}")

    def _update_pattern_label(self) -> None:
        self.pattern_label.setText(f"Organised as: {self.settings.pattern}")
        self.destination.setText(self.settings.destination)

    def _apply_settings(self, settings: Settings) -> None:
        rescan = (settings.include_photos, settings.include_videos, settings.scan_all_folders) != (
            self.settings.include_photos,
            self.settings.include_videos,
            self.settings.scan_all_folders,
        )
        self.settings = settings
        settings.save()
        self._update_pattern_label()
        if rescan and self._selected() and self._selected().accessible and not self._busy():
            self._start_scan()

    # ---------- device events ----------

    def _device_item(self, pnp_id: str) -> QListWidgetItem | None:
        for row in range(self.device_list.count()):
            item = self.device_list.item(row)
            if item.data(Qt.UserRole) == pnp_id:
                return item
        return None

    def _render_device_item(self, item: QListWidgetItem, info: wpd.DeviceInfo) -> None:
        kind = _KIND_LABEL[info.kind]
        state = "Ready" if info.accessible else "Locked or not trusted"
        item.setText(f"{info.display_name}\n{kind} - {state}")
        item.setIcon(self._icon_phone if info.accessible else self._icon_locked)
        item.setToolTip(info.hint if not info.accessible else f"{info.manufacturer} {info.model}".strip())

    def _on_device_added(self, info: wpd.DeviceInfo) -> None:
        self._devices[info.pnp_id] = info
        item = QListWidgetItem()
        item.setData(Qt.UserRole, info.pnp_id)
        self._render_device_item(item, info)
        self.device_list.addItem(item)
        self._log(f"Connected: {info.display_name} ({_KIND_LABEL[info.kind]})")
        self.statusBar().showMessage(f"{info.display_name} connected")
        if self.device_list.currentItem() is None and not self._busy():
            self.device_list.setCurrentItem(item)

    def _on_device_changed(self, info: wpd.DeviceInfo) -> None:
        self._devices[info.pnp_id] = info
        item = self._device_item(info.pnp_id)
        if item:
            self._render_device_item(item, info)
        if info.accessible:
            self._log(f"{info.display_name} is now accessible")
        if self._selected() and self._selected().pnp_id == info.pnp_id:
            self._on_selection_changed()

    def _on_device_removed(self, pnp_id: str) -> None:
        info = self._devices.pop(pnp_id, None)
        item = self._device_item(pnp_id)
        if item:
            self.device_list.takeItem(self.device_list.row(item))
        if info:
            self._log(f"Disconnected: {info.display_name}")
            self.statusBar().showMessage(f"{info.display_name} disconnected")
            if self._scan and self._scan.device_id == info.identity and not self._busy():
                self._scan = None
        if self._backup_pnp == pnp_id and self._backup_worker and self._backup_worker.isRunning():
            self._log("Device disconnected during backup; stopping.")
            self._backup_worker.requestInterruption()
        if not self._busy():
            self._on_selection_changed()

    def _on_selection_changed(self) -> None:
        info = self._selected()
        if info is None:
            self.device_title.setText("No device selected")
            self.device_status.setText("")
            self.scan_summary.setText("")
            self._refresh_controls()
            return
        self.device_title.setText(info.display_name)
        details = " ".join(p for p in (_KIND_LABEL[info.kind], "-", info.manufacturer, info.model) if p)
        if info.accessible:
            self.device_status.setText(details)
            self.device_status.setStyleSheet("")
        else:
            self.device_status.setText(f"{details}\n{info.hint}")
            self.device_status.setStyleSheet("color: #b9770e;")
        if self._scan and self._scan.device_id == info.identity:
            self._show_scan_summary(self._scan)
        else:
            self.scan_summary.setText("")
            if info.accessible and not self._busy():
                self._start_scan()
        self._refresh_controls()

    # ---------- scan ----------

    def _start_scan(self) -> None:
        info = self._selected()
        if not info or self._busy():
            return
        self._scan = None
        self.scan_summary.setText("Scanning device for photos and videos...")
        self.progress.setRange(0, 0)
        self.progress_label.setText("")
        self.watcher.pause_probe(info.pnp_id, True)
        worker = ScanWorker(info.pnp_id, info.identity, self.settings, self)
        worker.progress.connect(lambda n, folder: self.progress_label.setText(f"Found {n} files - {folder}"))
        worker.finished_ok.connect(self._on_scan_finished)
        worker.failed.connect(self._on_scan_failed)
        worker.finished.connect(lambda pnp=info.pnp_id: self._on_worker_done(pnp, False))
        self._scan_worker = worker
        self._log(f"Scanning {info.display_name}...")
        worker.start()
        self._refresh_controls()

    def _on_scan_finished(self, result: ScanResult) -> None:
        self._scan = result
        self._show_scan_summary(result)
        self._log(
            f"Scan complete: {len(result.items)} media files, {len(result.new_items)} new, "
            f"{result.already_backed_up} already backed up"
        )
        if self._scan_worker and self._scan_worker.isInterruptionRequested():
            self._log("Scan was cancelled; results may be incomplete.")

    def _show_scan_summary(self, result: ScanResult) -> None:
        if not result.items:
            self.scan_summary.setText(
                "No photos or videos found. On an iPhone, make sure it is unlocked; "
                "on Android, make sure 'File transfer' is selected."
            )
            return
        live = f", including {result.new_live_photos} Live Photo videos" if result.new_live_photos else ""
        self.scan_summary.setText(
            f"<b>{len(result.new_items)}</b> new ({result.count('photo')} photos, {result.count('video')} videos{live}, "
            f"{_human_bytes(result.new_bytes)}) &nbsp;|&nbsp; {result.already_backed_up} already backed up "
            f"&nbsp;|&nbsp; {len(result.items)} total on device"
        )

    def _on_scan_failed(self, message: str) -> None:
        self.scan_summary.setText(f"Scan failed: {message}")
        self._log(f"Scan failed: {message}")

    def _on_worker_done(self, pnp_id: str, was_backup: bool) -> None:
        self.watcher.pause_probe(pnp_id, False)
        self.progress.setRange(0, 1000)
        if not was_backup:
            self.progress_label.setText("")
        self._refresh_controls()
        info = self._selected()
        if was_backup and info and info.pnp_id == pnp_id and info.accessible:
            self._start_scan()

    # ---------- backup ----------

    def _start_backup(self) -> None:
        info = self._selected()
        if not info or not self._scan or self._busy() or not self._scan.new_items:
            return
        dest = Path(self.settings.destination)
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, APP_NAME, f"Cannot use destination folder:\n{exc}")
            return
        free = _free_space(dest)
        if free is not None and free < self._scan.new_bytes:
            answer = QMessageBox.question(
                self,
                APP_NAME,
                f"The destination has {_human_bytes(free)} free but the backup needs about "
                f"{_human_bytes(self._scan.new_bytes)}. Continue anyway?",
            )
            if answer != QMessageBox.Yes:
                return

        items = self._scan.new_items
        self._bytes_done = 0
        self._bytes_total = max(1, self._scan.new_bytes)
        self._started_at = time.monotonic()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.watcher.pause_probe(info.pnp_id, True)
        self._backup_pnp = info.pnp_id
        worker = BackupWorker(
            info.pnp_id, info.identity, info.display_name, items, self.settings, self._scan.live_pairs, parent=self
        )
        worker.file_started.connect(self._on_file_started)
        worker.bytes_copied.connect(self._on_bytes)
        worker.log_line.connect(self._log)
        worker.finished_ok.connect(self._on_backup_finished)
        worker.failed.connect(self._on_backup_failed)
        worker.finished.connect(lambda pnp=info.pnp_id: self._on_worker_done(pnp, True))
        self._backup_worker = worker
        self._log(f"Backing up {len(items)} files to {dest}")
        worker.start()
        self._refresh_controls()

    def _on_file_started(self, position: int, total: int, name: str) -> None:
        elapsed = time.monotonic() - self._started_at
        rate = self._bytes_done / elapsed if elapsed > 1 else 0
        eta = ""
        if rate > 0:
            remaining = (self._bytes_total - self._bytes_done) / rate
            eta = f" - about {int(remaining // 60)}m {int(remaining % 60)}s left"
        speed = f" - {_human_bytes(rate)}/s" if rate else ""
        self.progress_label.setText(f"File {position} of {total}: {name}{speed}{eta}")

    def _on_bytes(self, n: int) -> None:
        self._bytes_done += n
        self.progress.setValue(min(1000, int(self._bytes_done * 1000 / self._bytes_total)))

    def _on_backup_finished(self, summary: BackupSummary) -> None:
        self.progress.setValue(1000 if not (summary.cancelled or summary.aborted_reason) else self.progress.value())
        headline = "Backup cancelled" if summary.cancelled else "Backup stopped" if summary.aborted_reason else "Backup complete"
        lines = [
            f"Copied: {summary.copied} ({_human_bytes(summary.bytes_copied)})",
            f"Already backed up: {summary.skipped_existing}",
            f"Duplicates skipped: {summary.skipped_duplicate}",
            f"Failed: {summary.failed}",
        ]
        if summary.aborted_reason:
            lines.append("")
            lines.append(summary.aborted_reason)
        if summary.errors:
            lines.append("")
            lines.append("First errors:")
            lines.extend(summary.errors[:5])
        self.progress_label.setText(f"{headline}: {summary.copied} copied, {summary.failed} failed")
        self._log(f"{headline}. " + "; ".join(lines[:4]))

        box = QMessageBox(self)
        box.setWindowTitle(APP_NAME)
        box.setIcon(QMessageBox.Warning if summary.failed or summary.aborted_reason else QMessageBox.Information)
        box.setText(f"<b>{headline}</b>")
        box.setInformativeText("\n".join(lines))
        open_btn = box.addButton("Open destination folder", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Close)
        box.exec()
        if box.clickedButton() is open_btn:
            self._open_destination()

    def _on_backup_failed(self, message: str) -> None:
        self.progress_label.setText(f"Backup failed: {message}")
        self._log(f"Backup failed: {message}")
        QMessageBox.warning(self, APP_NAME, f"Backup failed:\n{message}")

    # ---------- actions ----------

    def _start_diagnostics(self) -> None:
        info = self._selected()
        if not info or self._busy():
            return
        safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in info.display_name).strip() or "device"
        default = Path.home() / "Desktop" / f"PhotoBackup diagnostics - {safe_name} - {time.strftime('%Y%m%d-%H%M%S')}.txt"
        path, _ = QFileDialog.getSaveFileName(self, "Save device diagnostics", str(default), "Text files (*.txt)")
        if not path:
            return
        self.watcher.pause_probe(info.pnp_id, True)
        self.progress.setRange(0, 0)
        worker = DiagnoseWorker(info.pnp_id, Path(path), app_data_dir() / "photobackup.log", self)
        worker.progress.connect(lambda folder: self.progress_label.setText(f"Inspecting {folder}"))
        worker.finished_ok.connect(self._on_diagnostics_finished)
        worker.failed.connect(self._on_diagnostics_failed)
        worker.finished.connect(lambda pnp=info.pnp_id: self._on_worker_done(pnp, False))
        self._diagnose_worker = worker
        self._log(f"Writing diagnostics for {info.display_name}...")
        worker.start()
        self._refresh_controls()

    def _on_diagnostics_failed(self, message: str) -> None:
        self._log(f"Diagnostics failed: {message}")
        QMessageBox.warning(self, APP_NAME, f"Could not write diagnostics:\n{message}")

    def _on_diagnostics_finished(self, path: str) -> None:
        self._log(f"Diagnostics saved to {path}")
        box = QMessageBox(self)
        box.setWindowTitle(APP_NAME)
        box.setIcon(QMessageBox.Information)
        box.setText("<b>Diagnostics saved</b>")
        box.setInformativeText(f"{path}\n\nThe report lists folder and file names from the device; review it before sharing.")
        open_btn = box.addButton("Open report", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Close)
        box.exec()
        if box.clickedButton() is open_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _cancel(self) -> None:
        for worker in self._workers():
            if worker is not None and worker.isRunning():
                worker.requestInterruption()
        self._log("Cancelling...")

    def _choose_destination(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose backup folder", self.settings.destination)
        if folder:
            self._apply_settings(replace(self.settings, destination=os.path.normpath(folder)))

    def _open_destination(self) -> None:
        dest = Path(self.settings.destination)
        dest.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(dest)))

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec():
            self._apply_settings(dialog.result_settings())

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._backup_worker and self._backup_worker.isRunning():
            answer = QMessageBox.question(self, APP_NAME, "A backup is in progress. Stop it and exit?")
            if answer != QMessageBox.Yes:
                event.ignore()
                return
        for worker in self._workers():
            if worker is not None and worker.isRunning():
                worker.requestInterruption()
                worker.wait(15000)
        self.watcher.stop()
        event.accept()


def _free_space(path: Path) -> int | None:
    try:
        import shutil

        return shutil.disk_usage(path).free
    except OSError:
        return None
