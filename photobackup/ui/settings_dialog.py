from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from photobackup.config import DUPLICATE_KEEP_BOTH, DUPLICATE_SKIP, Settings
from photobackup.core import organiser

PATTERN_PRESETS = [
    "{year}/{month:02} - {month_name}/{filename}",
    "{type}/{year}/{month:02} - {month_name}/{filename}",
    "{device}/{year}/{month:02} - {month_name}/{filename}",
    "{year}/{year}-{month:02}-{day:02}/{filename}",
    "{year}/{month:02}/{year}{month:02}{day:02}_{hour:02}{minute:02}{second:02}_{stem}",
    "{camera}/{year}/{filename}",
]


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(620)
        self._settings = settings

        self.destination = QLineEdit(settings.destination)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        dest_row = QHBoxLayout()
        dest_row.addWidget(self.destination, 1)
        dest_row.addWidget(browse)

        self.pattern = QComboBox()
        self.pattern.setEditable(True)
        self.pattern.addItems(PATTERN_PRESETS)
        self.pattern.setCurrentText(settings.pattern)
        self.pattern.currentTextChanged.connect(self._update_preview)

        self.preview = QLabel()
        self.preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.preview.setWordWrap(True)

        tokens = QLabel(
            "<br>".join(f"<code>{{{name}}}</code> &nbsp; {desc}" for name, desc in organiser.TOKENS.items())
        )
        tokens.setTextFormat(Qt.RichText)
        tokens.setStyleSheet("color: palette(mid);")

        organise = QGroupBox("Organisation")
        organise_form = QFormLayout(organise)
        organise_form.addRow("Folder pattern:", self.pattern)
        organise_form.addRow("Preview:", self.preview)
        organise_form.addRow("Tokens:", tokens)

        self.include_photos = QCheckBox("Photos")
        self.include_photos.setChecked(settings.include_photos)
        self.include_videos = QCheckBox("Videos")
        self.include_videos.setChecked(settings.include_videos)
        types_row = QHBoxLayout()
        types_row.addWidget(self.include_photos)
        types_row.addWidget(self.include_videos)
        types_row.addStretch(1)

        self.scan_all = QCheckBox("Scan the whole device, not just DCIM / Pictures / Movies (slower)")
        self.scan_all.setChecked(settings.scan_all_folders)

        self.duplicates = QComboBox()
        self.duplicates.addItem("Skip files whose content is already backed up", DUPLICATE_SKIP)
        self.duplicates.addItem("Keep both copies", DUPLICATE_KEEP_BOTH)
        self.duplicates.setCurrentIndex(max(0, self.duplicates.findData(settings.duplicate_policy)))

        general = QGroupBox("Backup")
        general_form = QFormLayout(general)
        general_form.addRow("Destination:", dest_row)
        general_form.addRow("Include:", types_row)
        general_form.addRow("", self.scan_all)
        general_form.addRow("Duplicates:", self.duplicates)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(general)
        layout.addWidget(organise)
        layout.addWidget(self.buttons)

        self.include_photos.toggled.connect(self._update_ok)
        self.include_videos.toggled.connect(self._update_ok)
        self.destination.textChanged.connect(self._update_ok)
        self._update_preview()

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose backup folder", self.destination.text())
        if folder:
            self.destination.setText(folder)

    def _update_preview(self) -> None:
        pattern = self.pattern.currentText()
        problems = organiser.validate(pattern)
        if problems:
            self.preview.setStyleSheet("color: #c0392b;")
            self.preview.setText("\n".join(problems))
        else:
            self.preview.setStyleSheet("")
            self.preview.setText("\n".join(organiser.preview(pattern)))
        self._update_ok()

    def _update_ok(self) -> None:
        ok = (
            not organiser.validate(self.pattern.currentText())
            and bool(self.destination.text().strip())
            and (self.include_photos.isChecked() or self.include_videos.isChecked())
        )
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(ok)

    def result_settings(self) -> Settings:
        return replace(
            self._settings,
            destination=self.destination.text().strip(),
            pattern=self.pattern.currentText().strip(),
            include_photos=self.include_photos.isChecked(),
            include_videos=self.include_videos.isChecked(),
            scan_all_folders=self.scan_all.isChecked(),
            duplicate_policy=self.duplicates.currentData(),
        )
