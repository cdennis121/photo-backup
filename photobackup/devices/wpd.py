"""Thin wrapper over the Windows Portable Devices (WPD) COM API.

COM objects are bound to the thread that created them, so every thread that
talks to a device must create its own ``DeviceManager`` / ``Device`` after
calling ``com_thread()``.
"""
from __future__ import annotations

import ctypes
import logging
from contextlib import contextmanager
from ctypes import POINTER, byref, c_double, c_ulong, c_ulonglong, c_ushort, c_void_p, c_wchar_p, cast
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import BinaryIO, Callable, Iterator

import comtypes
import comtypes.client
from comtypes import COMError, GUID

log = logging.getLogger(__name__)

_ole32 = ctypes.windll.ole32
_ole32.CoTaskMemFree.argtypes = [c_void_p]
_ole32.CoTaskMemFree.restype = None
_ole32.PropVariantClear.argtypes = [c_void_p]

_api = None
_types = None


def _libs():
    global _api, _types
    if _api is None:
        _api = comtypes.client.GetModule("portabledeviceapi.dll")
        _types = comtypes.client.GetModule("portabledevicetypes.dll")
    return _api, _types


@contextmanager
def com_thread():
    try:
        comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)
    except OSError:
        yield  # already initialised in a different mode on this thread
        return
    try:
        yield
    finally:
        comtypes.CoUninitialize()


_OBJECT_FMTID = "{EF6B490D-5CD8-437A-AFFC-DA8B60EE4A3C}"
_DEVICE_FMTID = "{26D4979A-E643-4626-9E2B-736DC0C92FDC}"
_RESOURCE_FMTID = "{E81E79BE-34F0-41BF-B53F-F1A06AE87842}"
_CLIENT_FMTID = "{204D9F0C-2292-4080-9F42-40664E70F859}"

WPD_DEVICE_OBJECT_ID = "DEVICE"

CONTENT_TYPE_FOLDER = "{27E2E392-A111-48E0-AB0C-E17705A05F85}"
CONTENT_TYPE_FUNCTIONAL_OBJECT = "{99ED0160-17FF-4C44-9D98-1D7A6F941921}"
CONTENT_TYPE_IMAGE = "{EF2107D5-A52A-4243-A26B-62D4176D7603}"
CONTENT_TYPE_VIDEO = "{9261B03C-3D78-4519-85E3-02C5E1F50BB9}"

_CONTENT_TYPE_NAMES = {
    CONTENT_TYPE_FOLDER: "Folder",
    CONTENT_TYPE_FUNCTIONAL_OBJECT: "Storage",
    CONTENT_TYPE_IMAGE: "Image",
    CONTENT_TYPE_VIDEO: "Video",
}

WPD_DEVICE_TYPE_PHONE = 3

_VT_LPWSTR = 31
_VT_DATE = 7
_VT_FILETIME = 64
_STGM_READ = 0

KIND_IPHONE = "iphone"
KIND_ANDROID = "android"
KIND_OTHER = "other"


def _key(fmtid: str, pid: int):
    api, _ = _libs()
    return api._tagpropertykey(GUID(fmtid), pid)


class Keys:
    """PROPERTYKEY constants, built lazily because they need the typelib."""

    _cache: dict[str, object] = {}
    _defs = {
        "OBJECT_PERSISTENT_UNIQUE_ID": (_OBJECT_FMTID, 5),
        "OBJECT_NAME": (_OBJECT_FMTID, 4),
        "OBJECT_CONTENT_TYPE": (_OBJECT_FMTID, 7),
        "OBJECT_SIZE": (_OBJECT_FMTID, 11),
        "OBJECT_ORIGINAL_FILE_NAME": (_OBJECT_FMTID, 12),
        "OBJECT_DATE_CREATED": (_OBJECT_FMTID, 18),
        "OBJECT_DATE_MODIFIED": (_OBJECT_FMTID, 19),
        "OBJECT_DATE_AUTHORED": (_OBJECT_FMTID, 20),
        "DEVICE_MANUFACTURER": (_DEVICE_FMTID, 7),
        "DEVICE_MODEL": (_DEVICE_FMTID, 8),
        "DEVICE_SERIAL_NUMBER": (_DEVICE_FMTID, 9),
        "DEVICE_FRIENDLY_NAME": (_DEVICE_FMTID, 12),
        "DEVICE_TYPE": (_DEVICE_FMTID, 15),
        "RESOURCE_DEFAULT": (_RESOURCE_FMTID, 0),
        "CLIENT_NAME": (_CLIENT_FMTID, 2),
        "CLIENT_MAJOR_VERSION": (_CLIENT_FMTID, 3),
        "CLIENT_MINOR_VERSION": (_CLIENT_FMTID, 4),
        "CLIENT_REVISION": (_CLIENT_FMTID, 5),
    }

    @classmethod
    def get(cls, name: str):
        if name not in cls._cache:
            cls._cache[name] = _key(*cls._defs[name])
        return cls._cache[name]


def format_error(exc: BaseException) -> str:
    """Readable text for a comtypes COMError, whose str() is a raw tuple."""
    args = getattr(exc, "args", ())
    if len(args) >= 2 and isinstance(args[0], int) and isinstance(args[1], str):
        return f"{args[1].strip()} (0x{args[0] & 0xFFFFFFFF:08X})"
    return str(exc)


def _take_wstr(address: int | None) -> str:
    if not address:
        return ""
    try:
        return ctypes.wstring_at(address)
    finally:
        _ole32.CoTaskMemFree(address)


def _ole_date(value: float) -> datetime | None:
    try:
        return datetime(1899, 12, 30) + timedelta(days=value)
    except (OverflowError, ValueError):
        return None


def _filetime(value: int) -> datetime | None:
    try:
        return datetime(1601, 1, 1) + timedelta(microseconds=value // 10)
    except (OverflowError, ValueError):
        return None


class _Values:
    """Helpers for reading an IPortableDeviceValues without leaking CoTaskMem strings."""

    def __init__(self, values) -> None:
        self._v = values

    def string(self, name: str) -> str:
        api, _ = _libs()
        out = c_void_p()
        raw = self._v._IPortableDeviceValues__com_GetStringValue
        try:
            raw(byref(Keys.get(name)), cast(ctypes.pointer(out), POINTER(c_wchar_p)))
        except COMError:
            return ""
        return _take_wstr(out.value)

    def uint64(self, name: str) -> int | None:
        try:
            return int(self._v.GetUnsignedLargeIntegerValue(Keys.get(name)))
        except COMError:
            return None

    def uint32(self, name: str) -> int | None:
        try:
            return int(self._v.GetUnsignedIntegerValue(Keys.get(name)))
        except COMError:
            return None

    def guid(self, name: str) -> str:
        try:
            return str(self._v.GetGuidValue(Keys.get(name))).upper()
        except COMError:
            return ""

    def date(self, name: str) -> datetime | None:
        api, _ = _libs()
        pv = api.tag_inner_PROPVARIANT()
        raw = self._v._IPortableDeviceValues__com_GetValue
        try:
            raw(byref(Keys.get(name)), byref(pv))
        except COMError:
            return None
        try:
            payload = ctypes.addressof(pv) + 8
            if pv.vt == _VT_DATE:
                return _ole_date(c_double.from_address(payload).value)
            if pv.vt == _VT_FILETIME:
                return _filetime(c_ulonglong.from_address(payload).value)
            if pv.vt == _VT_LPWSTR:
                address = c_void_p.from_address(payload).value
                return _parse_date_string(ctypes.wstring_at(address)) if address else None
            return None
        finally:
            _ole32.PropVariantClear(byref(pv))


def _parse_date_string(text: str) -> datetime | None:
    text = text.strip().rstrip("Z")
    for fmt in ("%Y/%m/%d:%H:%M:%S", "%Y%m%dT%H%M%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return None


@dataclass(frozen=True)
class DeviceInfo:
    pnp_id: str
    friendly_name: str
    manufacturer: str
    model: str
    serial: str
    kind: str
    accessible: bool
    error: str = ""

    @property
    def identity(self) -> str:
        """Stable id for the index; serial survives port changes, PnP id may not."""
        return f"{self.manufacturer}:{self.model}:{self.serial}" if self.serial else self.pnp_id

    @property
    def display_name(self) -> str:
        return self.friendly_name or self.model or "Unknown device"

    @property
    def hint(self) -> str:
        if self.kind == KIND_IPHONE:
            return "Unlock the iPhone and tap 'Trust' when asked. Apple Devices or iTunes must be installed."
        if self.kind == KIND_ANDROID:
            return "Unlock the phone and set USB mode to 'File transfer' (MTP) in the notification shade."
        return "Unlock the device and make sure it is in file transfer mode."


@dataclass(frozen=True)
class WpdObject:
    object_id: str
    name: str
    is_folder: bool
    content_type: str
    size: int | None
    date_created: datetime | None
    date_modified: datetime | None
    persistent_id: str


def _is_excluded_pnp(pnp_id: str) -> bool:
    lowered = pnp_id.lower()
    return any(tag in lowered for tag in ("usbstor", "storage#volume", "scsi#", "#sd#"))


def _classify(manufacturer: str, model: str, friendly: str, device_type: int | None) -> str:
    text = " ".join((manufacturer, model, friendly)).lower()
    if "apple" in text or "iphone" in text or "ipad" in text:
        return KIND_IPHONE
    if device_type == WPD_DEVICE_TYPE_PHONE or any(
        brand in text
        for brand in ("android", "samsung", "google", "pixel", "xiaomi", "oneplus", "motorola", "huawei", "oppo", "sony", "nokia", "lg", "realme", "vivo", "honor", "nothing")
    ):
        return KIND_ANDROID
    return KIND_OTHER


class DeviceManager:
    def __init__(self) -> None:
        api, _ = _libs()
        self._mgr = comtypes.client.CreateObject(api.PortableDeviceManager, interface=api.IPortableDeviceManager)

    def pnp_ids(self) -> list[str]:
        try:
            self._mgr.RefreshDeviceList()
        except COMError:
            pass
        raw = self._mgr._IPortableDeviceManager__com_GetDevices
        count = c_ulong(0)
        raw(None, byref(count))
        if count.value == 0:
            return []
        arr = (c_void_p * count.value)()
        raw(cast(arr, POINTER(c_wchar_p)), byref(count))
        ids = [_take_wstr(arr[i]) for i in range(count.value)]
        return [i for i in ids if i and not _is_excluded_pnp(i)]

    def _string(self, method: str, pnp_id: str) -> str:
        raw = getattr(self._mgr, f"_IPortableDeviceManager__com_{method}")
        size = c_ulong(0)
        try:
            raw(pnp_id, None, byref(size))
            if size.value == 0:
                return ""
            buf = ctypes.create_unicode_buffer(size.value)
            raw(pnp_id, cast(buf, POINTER(c_ushort)), byref(size))
            return buf.value
        except COMError:
            return ""

    def describe(self, pnp_id: str) -> DeviceInfo:
        friendly = self._string("GetDeviceFriendlyName", pnp_id)
        manufacturer = self._string("GetDeviceManufacturer", pnp_id)
        model = serial = error = ""
        device_type = None
        accessible = False
        try:
            with Device(pnp_id) as dev:
                props = dev.device_properties()
                manufacturer = props.string("DEVICE_MANUFACTURER") or manufacturer
                model = props.string("DEVICE_MODEL")
                serial = props.string("DEVICE_SERIAL_NUMBER")
                friendly = props.string("DEVICE_FRIENDLY_NAME") or friendly
                device_type = props.uint32("DEVICE_TYPE")
                accessible = any(True for _ in dev.children(WPD_DEVICE_OBJECT_ID))
        except COMError as exc:
            error = format_error(exc)
            log.info("Could not open %s: %s", pnp_id, error)
        return DeviceInfo(
            pnp_id=pnp_id,
            friendly_name=friendly,
            manufacturer=manufacturer,
            model=model,
            serial=serial,
            kind=_classify(manufacturer, model, friendly, device_type),
            accessible=accessible,
            error=error,
        )


class Device:
    """An open connection to a single WPD device."""

    _OBJECT_KEYS = (
        "OBJECT_NAME",
        "OBJECT_ORIGINAL_FILE_NAME",
        "OBJECT_CONTENT_TYPE",
        "OBJECT_SIZE",
        "OBJECT_DATE_CREATED",
        "OBJECT_DATE_MODIFIED",
        "OBJECT_DATE_AUTHORED",
        "OBJECT_PERSISTENT_UNIQUE_ID",
    )

    def __init__(self, pnp_id: str) -> None:
        api, types = _libs()
        self.pnp_id = pnp_id
        client = comtypes.client.CreateObject(types.PortableDeviceValues, interface=api.IPortableDeviceValues)
        client.SetStringValue(Keys.get("CLIENT_NAME"), "PhotoBackup")
        client.SetUnsignedIntegerValue(Keys.get("CLIENT_MAJOR_VERSION"), 1)
        client.SetUnsignedIntegerValue(Keys.get("CLIENT_MINOR_VERSION"), 0)
        client.SetUnsignedIntegerValue(Keys.get("CLIENT_REVISION"), 0)
        self._device = comtypes.client.CreateObject(api.PortableDeviceFTM, interface=api.IPortableDevice)
        self._device.Open(pnp_id, client)
        self._content = self._device.Content()
        self._properties = self._content.Properties()
        self._object_keys = self._make_keys(self._OBJECT_KEYS)
        self._device_keys = self._make_keys(
            ("DEVICE_MANUFACTURER", "DEVICE_MODEL", "DEVICE_SERIAL_NUMBER", "DEVICE_FRIENDLY_NAME", "DEVICE_TYPE")
        )

    def __enter__(self) -> "Device":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        try:
            self._device.Close()
        except COMError:
            pass

    def cancel(self) -> None:
        try:
            self._device.Cancel()
        except COMError:
            pass

    @staticmethod
    def _make_keys(names):
        api, types = _libs()
        keys = comtypes.client.CreateObject(types.PortableDeviceKeyCollection, interface=api.IPortableDeviceKeyCollection)
        for name in names:
            keys.Add(Keys.get(name))
        return keys

    def device_properties(self) -> _Values:
        return _Values(self._properties.GetValues(WPD_DEVICE_OBJECT_ID, self._device_keys))

    def children(self, parent_id: str) -> Iterator[str]:
        enum = self._content.EnumObjects(0, parent_id, None)
        raw_next = enum._IEnumPortableDeviceObjectIDs__com_Next
        batch = 64
        while True:
            arr = (c_void_p * batch)()
            fetched = c_ulong(0)
            raw_next(batch, cast(arr, POINTER(c_wchar_p)), byref(fetched))
            ids = [_take_wstr(arr[i]) for i in range(fetched.value)]
            yield from (i for i in ids if i)
            if fetched.value < batch:
                return

    def describe_object(self, object_id: str) -> WpdObject:
        v = _Values(self._properties.GetValues(object_id, self._object_keys))
        content_type = v.guid("OBJECT_CONTENT_TYPE")
        return WpdObject(
            object_id=object_id,
            name=v.string("OBJECT_ORIGINAL_FILE_NAME") or v.string("OBJECT_NAME"),
            is_folder=content_type in (CONTENT_TYPE_FOLDER, CONTENT_TYPE_FUNCTIONAL_OBJECT),
            content_type=content_type,
            size=v.uint64("OBJECT_SIZE"),
            date_created=v.date("OBJECT_DATE_AUTHORED") or v.date("OBJECT_DATE_CREATED"),
            date_modified=v.date("OBJECT_DATE_MODIFIED"),
            persistent_id=v.string("OBJECT_PERSISTENT_UNIQUE_ID"),
        )

    def raw_properties(self, object_id: str) -> dict[str, str]:
        """Every property we read, unprocessed, for diagnostics."""
        v = _Values(self._properties.GetValues(object_id, self._object_keys))
        dates = {n: v.date(n) for n in ("OBJECT_DATE_CREATED", "OBJECT_DATE_MODIFIED", "OBJECT_DATE_AUTHORED")}
        return {
            "name": v.string("OBJECT_NAME"),
            "original_name": v.string("OBJECT_ORIGINAL_FILE_NAME"),
            "content_type": _CONTENT_TYPE_NAMES.get(v.guid("OBJECT_CONTENT_TYPE"), v.guid("OBJECT_CONTENT_TYPE")),
            "size": "" if v.uint64("OBJECT_SIZE") is None else str(v.uint64("OBJECT_SIZE")),
            "created": str(dates["OBJECT_DATE_CREATED"] or ""),
            "modified": str(dates["OBJECT_DATE_MODIFIED"] or ""),
            "authored": str(dates["OBJECT_DATE_AUTHORED"] or ""),
            "persistent_id": v.string("OBJECT_PERSISTENT_UNIQUE_ID"),
        }

    def list_objects(self, parent_id: str) -> Iterator[WpdObject]:
        for object_id in self.children(parent_id):
            try:
                yield self.describe_object(object_id)
            except COMError as exc:
                log.warning("Skipping object %s: %s", object_id, exc)

    def copy_to(
        self,
        object_id: str,
        out: BinaryIO,
        on_chunk: Callable[[bytes], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> int:
        """Stream an object's default resource into ``out``. Returns bytes written."""
        api, _ = _libs()
        resources = self._content.Transfer()
        stream = POINTER(api.IStream)()
        optimal = c_ulong(0)
        resources._IPortableDeviceResources__com_GetStream(
            object_id, byref(Keys.get("RESOURCE_DEFAULT")), _STGM_READ, byref(optimal), byref(stream)
        )
        chunk_size = max(optimal.value, 256 * 1024)
        buf = (ctypes.c_ubyte * chunk_size)()
        read = c_ulong(0)
        raw_read = stream._ISequentialStream__com_RemoteRead
        total = 0
        while True:
            if should_cancel and should_cancel():
                raise InterruptedError("Copy cancelled")
            read.value = 0
            raw_read(buf, chunk_size, byref(read))
            if read.value == 0:
                break
            data = ctypes.string_at(buf, read.value)
            out.write(data)
            if on_chunk:
                on_chunk(data)
            total += read.value
        return total
