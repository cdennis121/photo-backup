import io
from datetime import datetime
from pathlib import Path

from PIL import Image

from photobackup.config import DUPLICATE_KEEP_BOTH
from photobackup.core.backup_worker import STAGING_DIR_NAME, BackupEngine, classify_new, order_with_pairs
from photobackup.core.index_db import IndexDB
from photobackup.core.scanner import MediaItem, live_photo_pairs

PATTERN = "{year}/{month:02} - {month_name}/{filename}"


def _jpeg(taken: str, colour: str = "red") -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", (8, 8), colour)
    exif = img.getexif()
    exif.get_ifd(0x8769)[36867] = taken
    img.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


class FakeSource:
    def __init__(self, blobs: dict[str, bytes], fail: set[str] = frozenset()) -> None:
        self.blobs = blobs
        self.fail = set(fail)

    def copy_to(self, object_id, out, on_chunk=None, should_cancel=None):
        if object_id in self.fail:
            raise OSError("device error")
        data = self.blobs[object_id]
        for i in range(0, len(data), 64):
            if should_cancel and should_cancel():
                raise InterruptedError()
            chunk = data[i : i + 64]
            out.write(chunk)
            if on_chunk:
                on_chunk(chunk)
        return len(data)


def _item(object_id: str, name: str, data: bytes, media_type: str = "photo", device_date=None) -> MediaItem:
    return MediaItem(object_id, name, len(data), media_type, device_date, f"pid-{object_id}", "DCIM/100APPLE")


def _engine(source, db, dest: Path, device_id="dev", **kw) -> BackupEngine:
    return BackupEngine(source, db, device_id=device_id, device_name="Phone", destination=dest, pattern=PATTERN, **kw)


def test_copies_organises_and_is_incremental(tmp_path: Path):
    photo = _jpeg("2024:07:14 10:11:12")
    video = b"\x00" * 300
    source = FakeSource({"p": photo, "v": video})
    items = [_item("p", "IMG_1.jpg", photo), _item("v", "VID_1.mp4", video, "video", datetime(2023, 1, 2, 3, 4, 5))]
    dest = tmp_path / "out"
    with IndexDB(tmp_path / "i.db") as db:
        summary = _engine(source, db, dest).run(items)
        assert (summary.copied, summary.failed) == (2, 0)
        target = dest / "2024" / "07 - July" / "IMG_1.jpg"
        assert target.read_bytes() == photo
        assert datetime.fromtimestamp(target.stat().st_mtime) == datetime(2024, 7, 14, 10, 11, 12)
        assert (dest / "2023" / "01 - January" / "VID_1.mp4").exists()
        assert not (dest / STAGING_DIR_NAME).exists()

        again = _engine(source, db, dest).run(items)
        assert (again.copied, again.skipped_existing) == (0, 2)
        assert classify_new(items, db.imported_set("dev")) == []


def test_duplicate_content_from_other_device_is_skipped(tmp_path: Path):
    photo = _jpeg("2024:07:14 10:11:12")
    dest = tmp_path / "out"
    with IndexDB(tmp_path / "i.db") as db:
        _engine(FakeSource({"a": photo}), db, dest, device_id="phone1").run([_item("a", "IMG_1.jpg", photo)])
        summary = _engine(FakeSource({"b": photo}), db, dest, device_id="phone2").run([_item("b", "COPY.jpg", photo)])
        assert (summary.copied, summary.skipped_duplicate) == (0, 1)
        assert _item("b", "COPY.jpg", photo) in db.imported_set("phone2")


def test_keep_both_policy_copies_duplicates_with_suffix(tmp_path: Path):
    photo = _jpeg("2024:07:14 10:11:12")
    other = _jpeg("2024:07:14 10:11:12", colour="blue")
    dest = tmp_path / "out"
    with IndexDB(tmp_path / "i.db") as db:
        _engine(FakeSource({"a": photo}), db, dest, device_id="p1").run([_item("a", "IMG_1.jpg", photo)])
        _engine(FakeSource({"b": photo}), db, dest, device_id="p2", duplicate_policy=DUPLICATE_KEEP_BOTH).run(
            [_item("b", "IMG_1.jpg", photo)]
        )
        _engine(FakeSource({"c": other}), db, dest, device_id="p3").run([_item("c", "IMG_1.jpg", other)])
    folder = dest / "2024" / "07 - July"
    assert sorted(p.name for p in folder.iterdir()) == ["IMG_1.jpg", "IMG_1_1.jpg", "IMG_1_2.jpg"]


def test_existing_identical_file_without_index_is_not_copied_twice(tmp_path: Path):
    photo = _jpeg("2024:07:14 10:11:12")
    dest = tmp_path / "out"
    with IndexDB(tmp_path / "first.db") as db:
        _engine(FakeSource({"a": photo}), db, dest).run([_item("a", "IMG_1.jpg", photo)])
    with IndexDB(tmp_path / "fresh.db") as db:
        summary = _engine(FakeSource({"a": photo}), db, dest).run([_item("a", "IMG_1.jpg", photo)])
    assert (summary.copied, summary.skipped_duplicate) == (0, 1)
    assert len(list((dest / "2024" / "07 - July").iterdir())) == 1


def test_failures_are_reported_and_cleaned_up(tmp_path: Path):
    photo = _jpeg("2024:07:14 10:11:12")
    source = FakeSource({"ok": photo, "bad": photo}, fail={"bad"})
    dest = tmp_path / "out"
    with IndexDB(tmp_path / "i.db") as db:
        summary = _engine(source, db, dest).run([_item("bad", "BAD.jpg", photo), _item("ok", "IMG_1.jpg", photo)])
    assert (summary.copied, summary.failed) == (1, 1)
    assert "BAD.jpg" in summary.errors[0]
    assert not (dest / STAGING_DIR_NAME).exists()


def test_aborts_after_repeated_failures(tmp_path: Path):
    data = b"x" * 10
    ids = [f"f{i}" for i in range(8)]
    source = FakeSource({i: data for i in ids}, fail=set(ids))
    with IndexDB(tmp_path / "i.db") as db:
        summary = _engine(source, db, tmp_path / "out").run([_item(i, f"{i}.jpg", data) for i in ids])
    assert summary.failed == 5 and summary.aborted_reason


def test_cancel_stops_and_removes_partial_file(tmp_path: Path):
    photo = _jpeg("2024:07:14 10:11:12")
    calls = {"n": 0}

    def cancel_midway():
        calls["n"] += 1
        return calls["n"] > 3

    dest = tmp_path / "out"
    with IndexDB(tmp_path / "i.db") as db:
        summary = _engine(FakeSource({"a": photo}), db, dest, should_cancel=cancel_midway).run(
            [_item("a", "IMG_1.jpg", photo)]
        )
        assert summary.cancelled and summary.copied == 0
        assert _item("a", "IMG_1.jpg", photo) not in db.imported_set("dev")
    assert not any(dest.rglob("*.jpg"))
    assert not any(dest.rglob("*.part"))


def test_invalid_pattern_aborts_before_copying(tmp_path: Path):
    photo = _jpeg("2024:07:14 10:11:12")
    with IndexDB(tmp_path / "i.db") as db:
        engine = BackupEngine(
            FakeSource({"a": photo}), db, device_id="d", device_name="P", destination=tmp_path / "out", pattern="{nope}"
        )
        summary = engine.run([_item("a", "IMG_1.jpg", photo)])
    assert summary.aborted_reason and summary.copied == 0


def test_reused_file_name_on_same_phone_is_backed_up(tmp_path: Path):
    old, new = b"A" * 100, b"B" * 100
    dest = tmp_path / "out"
    with IndexDB(tmp_path / "i.db") as db:
        first = _item("o1", "IMG_0001.jpg", old, device_date=datetime(2023, 3, 1, 9, 0, 0))
        _engine(FakeSource({"o1": old}), db, dest).run([first])
        reused = MediaItem("o2", "IMG_0001.jpg", 100, "photo", datetime(2025, 6, 1, 9, 0, 0), "", "DCIM/100APPLE")
        assert classify_new([first, reused], db.imported_set("dev")) == [reused]
        summary = _engine(FakeSource({"o2": new}), db, dest).run([reused])
    assert summary.copied == 1
    assert (dest / "2025" / "06 - June" / "IMG_0001.jpg").read_bytes() == new


def test_keep_both_never_stores_the_same_phone_content_twice(tmp_path: Path):
    photo = _jpeg("2024:07:14 10:11:12")
    with IndexDB(tmp_path / "i.db") as db:
        engine = _engine(FakeSource({"a": photo, "b": photo}), db, tmp_path / "out", duplicate_policy=DUPLICATE_KEEP_BOTH)
        summary = engine.run([_item("a", "IMG_1.jpg", photo), _item("b", "IMG_1 copy.jpg", photo)])
    assert (summary.copied, summary.skipped_duplicate) == (1, 1)


def _live_pair(still_taken: str, video_date: datetime):
    still_data = _jpeg(still_taken)
    video_data = b"\x01" * 500
    still = _item("s", "IMG_0001.JPG", still_data)
    video = _item("m", "IMG_0001.MOV", video_data, "video", video_date)
    return still, video, FakeSource({"s": still_data, "m": video_data})


def test_live_photo_video_follows_its_still_across_a_month_boundary(tmp_path: Path):
    still, video, source = _live_pair("2024:07:31 23:59:58", datetime(2024, 8, 1, 0, 0, 3))
    dest = tmp_path / "out"
    with IndexDB(tmp_path / "i.db") as db:
        pairs = live_photo_pairs([still, video])
        summary = _engine(source, db, dest).run([video, still], pairs)
    assert summary.copied == 2
    july = dest / "2024" / "07 - July"
    assert sorted(p.name for p in july.iterdir()) == ["IMG_0001.JPG", "IMG_0001.MOV"]
    assert not (dest / "2024" / "08 - August").exists()
    assert datetime.fromtimestamp((july / "IMG_0001.MOV").stat().st_mtime) == datetime(2024, 7, 31, 23, 59, 58)


def test_live_photo_video_takes_the_stills_renamed_stem(tmp_path: Path):
    still, video, source = _live_pair("2024:07:14 10:00:00", datetime(2024, 7, 14, 10, 0, 1))
    dest = tmp_path / "out"
    folder = dest / "2024" / "07 - July"
    folder.mkdir(parents=True)
    (folder / "IMG_0001.JPG").write_bytes(b"someone else's photo")
    with IndexDB(tmp_path / "i.db") as db:
        _engine(source, db, dest).run([still, video], live_photo_pairs([still, video]))
    assert {"IMG_0001_1.JPG", "IMG_0001_1.MOV"} <= {p.name for p in folder.iterdir()}


def test_live_photo_video_joins_a_still_from_an_earlier_backup(tmp_path: Path):
    still, video, source = _live_pair("2024:07:31 23:59:58", datetime(2024, 8, 1, 0, 0, 3))
    dest = tmp_path / "out"
    with IndexDB(tmp_path / "i.db") as db:
        _engine(source, db, dest).run([still])
        pairs = live_photo_pairs([still, video])
        new_items = classify_new([still, video], db.imported_set("dev"))
        assert new_items == [video]
        _engine(source, db, dest).run(new_items, pairs)
    assert (dest / "2024" / "07 - July" / "IMG_0001.MOV").exists()


def test_pairing_and_ordering():
    still, video, _ = _live_pair("2024:07:14 10:00:00", datetime(2024, 7, 14))
    other_folder = MediaItem("x", "IMG_0001.MOV", 5, "video", None, "", "DCIM/101APPLE")
    plain = _item("p", "IMG_0002.JPG", b"123")
    pairs = live_photo_pairs([still, video, other_folder, plain])
    assert pairs == {"m": still}
    ordered = order_with_pairs([video, plain, still], pairs)
    assert [i.object_id for i in ordered] == ["p", "s", "m"]
