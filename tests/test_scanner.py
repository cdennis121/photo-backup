from datetime import datetime

from photobackup.core.scanner import MediaScanner, media_type_for
from photobackup.devices import wpd


def _folder(oid, name):
    return wpd.WpdObject(oid, name, True, wpd.CONTENT_TYPE_FOLDER, None, None, None, "")


def _file(oid, name, size=100, content_type=""):
    return wpd.WpdObject(oid, name, False, content_type, size, datetime(2024, 1, 1), None, "")


class FakeDevice:
    def __init__(self, tree):
        self.tree = tree

    def list_objects(self, parent_id):
        return iter(self.tree.get(parent_id, []))


TREE = {
    "DEVICE": [wpd.WpdObject("s1", "Internal shared storage", True, wpd.CONTENT_TYPE_FUNCTIONAL_OBJECT, None, None, None, "")],
    "s1": [_folder("dcim", "DCIM"), _folder("android", "Android"), _folder("docs", "Documents"), _folder("pics", "Pictures")],
    "dcim": [_folder("cam", "Camera"), _folder("thumbs", ".thumbnails")],
    "cam": [_file("p1", "IMG_1.jpg"), _file("v1", "VID_1.mp4"), _file("z", "empty.jpg", size=0), _file("t", "notes.txt")],
    "thumbs": [_file("th", "thumb.jpg")],
    "pics": [_file("p2", "Screenshot.png"), _file("p3", "noext", content_type=wpd.CONTENT_TYPE_IMAGE)],
    "docs": [_file("d1", "scan.jpg")],
    "android": [_file("a1", "cache.jpg")],
}


def test_scans_media_roots_only_by_default():
    items = MediaScanner(FakeDevice(TREE)).scan()
    assert sorted(i.object_id for i in items) == ["p1", "p2", "p3", "v1"]
    video = next(i for i in items if i.object_id == "v1")
    assert video.media_type == "video" and video.folder == "Internal shared storage/DCIM/Camera"


def test_scan_all_folders_still_skips_system_folders():
    items = MediaScanner(FakeDevice(TREE), scan_all_folders=True).scan()
    assert sorted(i.object_id for i in items) == ["d1", "p1", "p2", "p3", "v1"]


def test_type_filters():
    only_videos = MediaScanner(FakeDevice(TREE), include_photos=False).scan()
    assert [i.object_id for i in only_videos] == ["v1"]


def test_falls_back_to_whole_storage_without_media_roots():
    tree = {
        "DEVICE": [wpd.WpdObject("s", "Card", True, wpd.CONTENT_TYPE_FUNCTIONAL_OBJECT, None, None, None, "")],
        "s": [_folder("f", "Holiday"), _file("x", "top.jpg")],
        "f": [_file("y", "IMG.HEIC")],
    }
    assert sorted(i.object_id for i in MediaScanner(FakeDevice(tree)).scan()) == ["x", "y"]


def test_media_type_for():
    assert media_type_for("a.HEIC") == "photo"
    assert media_type_for("a.MOV") == "video"
    assert media_type_for("a.txt") is None
    assert media_type_for("blob", wpd.CONTENT_TYPE_VIDEO) == "video"


def test_device_classification():
    assert wpd._classify("Apple Inc.", "iPhone", "Sam's iPhone", None) == wpd.KIND_IPHONE
    assert wpd._classify("SAMSUNG", "SM-S928B", "Galaxy S24", None) == wpd.KIND_ANDROID
    assert wpd._classify("Acme", "X1", "Gadget", wpd.WPD_DEVICE_TYPE_PHONE) == wpd.KIND_ANDROID
    assert wpd._classify("Canon", "EOS", "Camera", 1) == wpd.KIND_OTHER
    assert wpd._is_excluded_pnp(r"\\?\swd#wpdbusenum#_??_usbstor#disk&ven_sandisk#1234")
