import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from photobackup.core.index_db import ImportedRecord, IndexDB, dates_compatible

SHOT = datetime(2024, 5, 1, 12, 0, 0)


def _obj(name="IMG_1.jpg", size=1, persistent_id="", device_date=SHOT):
    return SimpleNamespace(name=name, size=size, persistent_id=persistent_id, device_date=device_date)


def _record(db, dest: Path, name="IMG_1.jpg", size=1, device_id="dev", **kw):
    dest.write_bytes(b"x")
    return db.record(device_id=device_id, object_name=name, size=size, sha256=kw.pop("sha", "abc"), dest_path=dest, **kw)


def test_record_and_lookup(tmp_path: Path):
    dest = tmp_path / "IMG_1.jpg"
    with IndexDB(tmp_path / "index.db") as db:
        _record(db, dest, taken_at=datetime(2024, 1, 1), device_date=SHOT, persistent_id="p1")
        imported = db.imported_set("dev")
        record = imported.find(_obj())
        assert record and record.dest_path == str(dest) and record.taken_at == datetime(2024, 1, 1)
        assert _obj(size=2) not in imported
        assert _obj() not in db.imported_set("other")
        assert db.find_by_hash("abc") == str(dest)
        assert db.find_by_hash("abc", device_id="dev") == str(dest)
        assert db.find_by_hash("abc", device_id="other") is None
        assert db.find_by_hash("nope") is None
        assert db.count() == 1


def test_reused_file_name_with_different_date_is_new(tmp_path: Path):
    with IndexDB(tmp_path / "index.db") as db:
        _record(db, tmp_path / "a.jpg", device_date=SHOT)
        imported = db.imported_set("dev")
        assert _obj(device_date=SHOT) in imported
        assert _obj(device_date=SHOT + timedelta(days=400)) not in imported
        assert _obj(device_date=SHOT + timedelta(minutes=30)) not in imported


def test_time_zone_shift_is_still_the_same_object(tmp_path: Path):
    with IndexDB(tmp_path / "index.db") as db:
        _record(db, tmp_path / "a.jpg", device_date=SHOT)
        imported = db.imported_set("dev")
        assert _obj(device_date=SHOT + timedelta(hours=1)) in imported
        assert _obj(device_date=SHOT - timedelta(hours=5, seconds=1)) in imported


def test_persistent_id_match_overrides_date(tmp_path: Path):
    with IndexDB(tmp_path / "index.db") as db:
        _record(db, tmp_path / "a.jpg", device_date=SHOT, persistent_id="obj-42")
        imported = db.imported_set("dev")
        assert _obj(persistent_id="obj-42", device_date=SHOT + timedelta(days=3)) in imported
        assert _obj(persistent_id="obj-99", device_date=SHOT + timedelta(days=3)) not in imported


def test_missing_dates_fall_back_to_name_and_size(tmp_path: Path):
    with IndexDB(tmp_path / "index.db") as db:
        _record(db, tmp_path / "a.jpg", device_date=None)
        assert _obj(device_date=SHOT) in db.imported_set("dev")


def test_deleted_backup_copy_is_not_treated_as_imported(tmp_path: Path):
    dest = tmp_path / "gone.jpg"
    with IndexDB(tmp_path / "index.db") as db:
        _record(db, dest, name="gone.jpg", sha="h")
        dest.unlink()
        assert _obj(name="gone.jpg") not in db.imported_set("dev")
        assert db.find_by_hash("h") is None


def test_persists_between_connections(tmp_path: Path):
    with IndexDB(tmp_path / "index.db") as db:
        _record(db, tmp_path / "a.jpg", device_date=SHOT)
    with IndexDB(tmp_path / "index.db") as db:
        assert _obj() in db.imported_set("dev")


def test_upgrades_old_database_without_device_date(tmp_path: Path):
    path = tmp_path / "old.db"
    dest = tmp_path / "a.jpg"
    dest.write_bytes(b"x")
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE files (id INTEGER PRIMARY KEY, device_id TEXT NOT NULL, object_name TEXT NOT NULL,"
        " size INTEGER NOT NULL, persistent_id TEXT, sha256 TEXT NOT NULL, taken_at TEXT, dest_path TEXT NOT NULL,"
        " imported_at TEXT NOT NULL);"
    )
    conn.execute(
        "INSERT INTO files (device_id, object_name, size, sha256, dest_path, imported_at) VALUES (?,?,?,?,?,?)",
        ("dev", "IMG_1.jpg", 1, "h", str(dest), "2025-01-01T00:00:00"),
    )
    conn.commit()
    conn.close()
    with IndexDB(path) as db:
        assert _obj() in db.imported_set("dev")
        _record(db, tmp_path / "b.jpg", name="IMG_2.jpg", device_date=SHOT)
        assert db.count() == 2


def test_dates_compatible():
    assert dates_compatible(None, SHOT)
    assert dates_compatible(SHOT, SHOT + timedelta(seconds=2))
    assert dates_compatible(SHOT, SHOT + timedelta(hours=14))
    assert not dates_compatible(SHOT, SHOT + timedelta(hours=15))
    assert not dates_compatible(SHOT, SHOT + timedelta(hours=2, minutes=30))


def test_record_match_requires_same_name_and_size():
    record = ImportedRecord("IMG_1.jpg", 10, "pid", SHOT, None, "x")
    assert not record.matches("IMG_2.jpg", 10, "pid", SHOT)
    assert not record.matches("IMG_1.jpg", 11, "pid", SHOT)
