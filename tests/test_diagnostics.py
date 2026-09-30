import io

from photobackup.devices import wpd
from photobackup.devices.diagnostics import DiagnosticsReport

INFO = wpd.DeviceInfo("pnp-1", "Sam's iPhone", "Apple Inc.", "iPhone 17 Pro", "SERIAL123456", "iphone", True)


def _props(name, content_type, size="", created="2024-07-14 10:00:00", pid="p"):
    return {
        "name": name,
        "original_name": name,
        "content_type": content_type,
        "size": size,
        "created": created,
        "modified": "",
        "authored": "",
        "persistent_id": pid,
    }


class FakeDevice:
    TREE = {
        "DEVICE": ["s1"],
        "s1": ["dcim"],
        "dcim": ["f100"],
        "f100": ["a", "b", "c", "d"],
    }
    PROPS = {
        "s1": _props("Internal Storage", "Storage"),
        "dcim": _props("DCIM", "Folder"),
        "f100": _props("100APPLE", "Folder"),
        "a": _props("IMG_0001.HEIC", "Image", "2000000"),
        "b": _props("IMG_0001.MOV", "Video", "3000000"),
        "c": _props("IMG_0002.JPG", "Image", "1000", created="", pid=""),
        "d": _props("notes.txt", "", "10"),
    }

    def children(self, parent_id):
        return iter(self.TREE.get(parent_id, []))

    def raw_properties(self, object_id):
        if object_id == "d":
            raise OSError("device refused")
        return self.PROPS[object_id]


def test_report_contents_and_stats():
    out = io.StringIO()
    stats = DiagnosticsReport(INFO, FakeDevice(), max_listed_files=2).write(out)
    text = out.getvalue()
    assert (stats.folders, stats.files, stats.photos, stats.videos, stats.live_pairs) == (3, 3, 2, 1, 1)
    assert stats.missing_date == 1 and stats.missing_persistent_id == 1
    assert "Cannot read d" in stats.errors[0]
    assert "********3456" in text and "SERIAL123456" not in text
    assert "media folders: DCIM" in text
    assert "3 files (3 photos/videos, 1 Live Photo pairs), showing 2:" in text
    assert "IMG_0002.JPG  |" not in text


def test_inspection_limit_is_reported():
    out = io.StringIO()
    stats = DiagnosticsReport(INFO, FakeDevice(), max_inspected=3).write(out)
    assert stats.not_inspected > 0
    assert "limit of 3 reached" in out.getvalue()


def test_report_without_open_device():
    locked = wpd.DeviceInfo("pnp-2", "Phone", "", "", "", "android", False, "Access denied")
    out = io.StringIO()
    DiagnosticsReport(locked, None).write(out)
    text = out.getvalue()
    assert "Accessible:   no" in text and "Access denied" in text and "-- Contents --" not in text


def test_includes_log_tail(tmp_path):
    log = tmp_path / "photobackup.log"
    log.write_text("first\nlast line\n", encoding="utf-8")
    out = io.StringIO()
    DiagnosticsReport(INFO, None, log_path=log).write(out)
    assert out.getvalue().rstrip().endswith("last line")
