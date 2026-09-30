# PyInstaller spec: `pyinstaller build.spec` -> dist\PhotoBackup\PhotoBackup.exe
# Run `python -c "import photobackup.devices.wpd as w; w._libs()"` once before building
# so comtypes has generated the WPD wrappers that get bundled below.
import re

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

with open("photobackup/__init__.py", encoding="utf-8") as fh:
    VERSION = re.search(r'__version__ = "([^"]+)"', fh.read()).group(1)
_numbers = tuple(int(n) for n in re.findall(r"\d+", VERSION)[:3])
VERSION_TUPLE = _numbers + (0,) * (4 - len(_numbers))

version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=VERSION_TUPLE, prodvers=VERSION_TUPLE),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    "040904B0",
                    [
                        StringStruct("FileDescription", "PhotoBackup - phone photo and video backup"),
                        StringStruct("ProductName", "PhotoBackup"),
                        StringStruct("FileVersion", VERSION),
                        StringStruct("ProductVersion", VERSION),
                        StringStruct("OriginalFilename", "PhotoBackup.exe"),
                        StringStruct("LegalCopyright", "GPL-3.0-or-later"),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

hiddenimports = collect_submodules("comtypes.gen") + ["pillow_heif", "pymediainfo"]
binaries = collect_dynamic_libs("pymediainfo") + collect_dynamic_libs("pillow_heif")
datas = collect_data_files("pymediainfo") + [
    ("photobackup/assets", "photobackup/assets"),
    ("LICENSE", "."),
    ("THIRD_PARTY_NOTICES.md", "."),
]

qt_excludes = [
    f"PySide6.{m}"
    for m in (
        "QtNetwork", "QtQml", "QtQuick", "QtQuickWidgets", "QtPdf", "QtPdfWidgets", "QtSvg", "QtOpenGL",
        "QtOpenGLWidgets", "QtWebEngineCore", "QtWebEngineWidgets", "QtMultimedia", "QtVirtualKeyboard",
        "Qt3DCore", "QtCharts", "QtDataVisualization", "QtSql", "QtTest",
    )
]

a = Analysis(
    ["run.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "numpy", *qt_excludes],
    noarchive=False,
)

# The PySide6 hook copies Qt DLLs (and plugins that need them) even when their Python
# modules are excluded. A widgets-only app never loads these.
_UNUSED_QT = re.compile(
    r"(opengl32sw\.dll|Qt6(Quick|Qml|Pdf|Network|OpenGL|VirtualKeyboard|Svg)\w*\.dll"
    r"|qtvirtualkeyboard|qsvg|qpdf|networkinformation|[\\/]tls[\\/])",
    re.IGNORECASE,
)
a.binaries = [b for b in a.binaries if not _UNUSED_QT.search(b[0])]
a.datas = [d for d in a.datas if not _UNUSED_QT.search(d[0])]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PhotoBackup",
    icon="photobackup/assets/app.ico",
    version=version_info,
    console=False,
    upx=False,
)

coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="PhotoBackup")
