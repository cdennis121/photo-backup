# Third-party notices

PhotoBackup is licensed under the GNU General Public License v3.0 (see `LICENSE`).
The Windows release bundles the following third-party components. Each one remains under its own licence, and all of them are compatible with GPL-3.0.

| Component | Used for | Licence | Source |
|---|---|---|---|
| Python | Runtime | PSF License | https://www.python.org |
| Qt 6 / PySide6 / Shiboken6 | User interface | LGPL-3.0 | https://www.qt.io, https://code.qt.io/cgit/pyside/pyside-setup.git |
| comtypes | Windows Portable Devices (COM) access | MIT | https://github.com/enthought/comtypes |
| Pillow | Reading photo EXIF metadata | MIT-CMU (HPND) | https://github.com/python-pillow/Pillow |
| pillow-heif | HEIC/HEIF support | BSD-3-Clause | https://github.com/bigcat88/pillow_heif |
| libheif | HEIF container (bundled by pillow-heif) | LGPL-3.0 | https://github.com/strukturag/libheif |
| libde265 | HEVC decoding (bundled by pillow-heif) | LGPL-3.0 | https://github.com/strukturag/libde265 |
| x265 | HEVC encoding library (bundled by pillow-heif, not used by PhotoBackup) | GPL-2.0-or-later | https://bitbucket.org/multicoreware/x265_git |
| pymediainfo | Reading video metadata | MIT | https://github.com/sbraz/pymediainfo |
| MediaInfo library | Reading video metadata (bundled by pymediainfo) | BSD-2-Clause | https://mediaarea.net/en/MediaInfo |
| platformdirs | Locating the settings folder | MIT | https://github.com/tox-dev/platformdirs |
| PyInstaller bootloader | Packaging the executable | GPL-2.0-or-later with bootloader exception | https://github.com/pyinstaller/pyinstaller |

The source code for PhotoBackup is available from the project repository. The LGPL components ship as separate, replaceable DLLs in the `_internal` folder of the release.
