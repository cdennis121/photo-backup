from __future__ import annotations

import ctypes
import logging
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from photobackup import APP_NAME
from photobackup.config import Settings, app_data_dir

ASSETS_DIR = Path(__file__).resolve().parent / "assets"


def app_icon() -> QIcon:
    icon = QIcon(str(ASSETS_DIR / "app.ico"))
    icon.addFile(str(ASSETS_DIR / "logo.png"))
    return icon


def _set_taskbar_identity() -> None:
    """Without an explicit AppUserModelID, Windows groups the window under python.exe and shows its icon."""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{APP_NAME}.Desktop")
    except (AttributeError, OSError):
        pass


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(app_data_dir() / "photobackup.log", encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )


def main() -> int:
    _configure_logging()
    _set_taskbar_identity()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setStyle("Fusion")
    app.setWindowIcon(app_icon())

    from photobackup.ui.main_window import MainWindow

    window = MainWindow(Settings.load())
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
