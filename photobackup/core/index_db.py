"""SQLite record of everything already backed up, used for incremental runs and de-duplication.

Open one ``IndexDB`` per thread; sqlite connections must not be shared across threads.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id            INTEGER PRIMARY KEY,
    device_id     TEXT NOT NULL,
    object_name   TEXT NOT NULL,
    size          INTEGER NOT NULL,
    persistent_id TEXT,
    sha256        TEXT NOT NULL,
    taken_at      TEXT,
    dest_path     TEXT NOT NULL,
    imported_at   TEXT NOT NULL,
    device_date   TEXT
);
CREATE INDEX IF NOT EXISTS idx_files_device_obj ON files (device_id, object_name, size);
CREATE INDEX IF NOT EXISTS idx_files_sha ON files (sha256);
"""

# Phones and Windows disagree about time zones and DST, so the same object can be
# reported shifted by whole hours between connections.
_DATE_TOLERANCE_SECONDS = 2
_MAX_ZONE_SHIFT_HOURS = 14


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def dates_compatible(a: datetime | None, b: datetime | None) -> bool:
    if a is None or b is None:
        return True
    delta = abs((a - b).total_seconds())
    if delta <= _DATE_TOLERANCE_SECONDS:
        return True
    hours, remainder = divmod(delta, 3600)
    near_whole_hour = remainder <= _DATE_TOLERANCE_SECONDS or remainder >= 3600 - _DATE_TOLERANCE_SECONDS
    return near_whole_hour and round(delta / 3600) <= _MAX_ZONE_SHIFT_HOURS


@dataclass(frozen=True)
class ImportedRecord:
    object_name: str
    size: int
    persistent_id: str
    device_date: datetime | None
    taken_at: datetime | None
    dest_path: str

    def matches(self, name: str, size: int, persistent_id: str, device_date: datetime | None) -> bool:
        """Same object on the phone? Name and size must match; then a matching persistent id
        or a compatible device date confirms it. Phone file names get reused (IMG_9999 wraps),
        so name and size alone are not enough."""
        if self.object_name != name or self.size != size:
            return False
        if self.persistent_id and persistent_id and self.persistent_id == persistent_id:
            return True
        return dates_compatible(self.device_date, device_date)


class ImportedSet:
    """In-memory view of one device's backed-up objects for fast bulk lookups."""

    def __init__(self, records: Iterable[ImportedRecord] = ()) -> None:
        self._by_key: dict[tuple[str, int], list[ImportedRecord]] = {}
        for record in records:
            self.add(record)

    def add(self, record: ImportedRecord) -> None:
        self._by_key.setdefault((record.object_name, record.size), []).append(record)

    def find(self, item) -> ImportedRecord | None:
        """``item`` is anything with name, size, persistent_id and device_date (a MediaItem)."""
        for record in self._by_key.get((item.name, item.size), ()):
            if record.matches(item.name, item.size, item.persistent_id, item.device_date):
                return record
        return None

    def __contains__(self, item) -> bool:
        return self.find(item) is not None

    def __len__(self) -> int:
        return sum(len(v) for v in self._by_key.values())


class IndexDB:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), timeout=30)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(files)")}
        if "device_date" not in columns:
            self._conn.execute("ALTER TABLE files ADD COLUMN device_date TEXT")

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "IndexDB":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def imported_set(self, device_id: str) -> ImportedSet:
        """Objects from this device whose backup copy still exists on disk."""
        rows = self._conn.execute(
            "SELECT object_name, size, persistent_id, device_date, taken_at, dest_path FROM files WHERE device_id = ?",
            (device_id,),
        ).fetchall()
        exists: dict[str, bool] = {}
        records = []
        for name, size, pid, device_date, taken_at, dest in rows:
            if dest not in exists:
                exists[dest] = Path(dest).exists()
            if exists[dest]:
                records.append(ImportedRecord(name, size, pid or "", _parse_iso(device_date), _parse_iso(taken_at), dest))
        return ImportedSet(records)

    def find_by_hash(self, sha256: str, device_id: str | None = None) -> str | None:
        if device_id is None:
            rows = self._conn.execute("SELECT dest_path FROM files WHERE sha256 = ?", (sha256,)).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT dest_path FROM files WHERE sha256 = ? AND device_id = ?", (sha256, device_id)
            ).fetchall()
        return next((dest for (dest,) in rows if Path(dest).exists()), None)

    def record(
        self,
        *,
        device_id: str,
        object_name: str,
        size: int,
        sha256: str,
        dest_path: Path | str,
        taken_at: datetime | None = None,
        persistent_id: str = "",
        device_date: datetime | None = None,
    ) -> ImportedRecord:
        self._conn.execute(
            "INSERT INTO files (device_id, object_name, size, persistent_id, sha256, taken_at, dest_path,"
            " imported_at, device_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                device_id,
                object_name,
                size,
                persistent_id,
                sha256,
                _iso(taken_at),
                str(dest_path),
                _iso(datetime.now()),
                _iso(device_date),
            ),
        )
        self._conn.commit()
        return ImportedRecord(
            object_name=object_name,
            size=size,
            persistent_id=persistent_id,
            device_date=_parse_iso(_iso(device_date)),
            taken_at=_parse_iso(_iso(taken_at)),
            dest_path=str(dest_path),
        )

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
