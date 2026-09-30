from __future__ import annotations

import logging

from PySide6.QtCore import QThread, Signal

from photobackup.devices import wpd

log = logging.getLogger(__name__)


class DeviceWatcher(QThread):
    """Polls WPD for phones. Devices that are not yet readable (locked, untrusted)
    are re-probed each poll so the UI updates as soon as the user unlocks them."""

    device_added = Signal(object)  # DeviceInfo
    device_changed = Signal(object)  # DeviceInfo
    device_removed = Signal(str)  # pnp_id
    watcher_error = Signal(str)

    def __init__(self, interval_seconds: int = 3, parent=None) -> None:
        super().__init__(parent)
        self._interval_ms = max(1, interval_seconds) * 1000
        self._known: dict[str, wpd.DeviceInfo] = {}
        self._paused: set[str] = set()

    def pause_probe(self, pnp_id: str, paused: bool) -> None:
        """Stop re-opening a device while a scan or backup is using it."""
        if paused:
            self._paused.add(pnp_id)
        else:
            self._paused.discard(pnp_id)

    def stop(self) -> None:
        self.requestInterruption()
        self.wait(self._interval_ms + 2000)

    def run(self) -> None:
        with wpd.com_thread():
            try:
                manager = wpd.DeviceManager()
            except Exception as exc:  # noqa: BLE001
                log.exception("WPD unavailable")
                self.watcher_error.emit(f"Windows Portable Devices is unavailable: {exc}")
                return
            while not self.isInterruptionRequested():
                try:
                    self._poll(manager)
                except Exception as exc:  # noqa: BLE001
                    log.exception("Device poll failed")
                    self.watcher_error.emit(str(exc))
                for _ in range(self._interval_ms // 100):
                    if self.isInterruptionRequested():
                        return
                    self.msleep(100)

    def _poll(self, manager: wpd.DeviceManager) -> None:
        current = set(manager.pnp_ids())
        for pnp_id in list(self._known):
            if pnp_id not in current:
                del self._known[pnp_id]
                self._paused.discard(pnp_id)
                self.device_removed.emit(pnp_id)
        for pnp_id in current:
            previous = self._known.get(pnp_id)
            if previous and (previous.accessible or pnp_id in self._paused):
                continue
            info = manager.describe(pnp_id)
            self._known[pnp_id] = info
            if previous is None:
                self.device_added.emit(info)
            elif info != previous:
                self.device_changed.emit(info)
