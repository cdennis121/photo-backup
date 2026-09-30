# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-09-30

First public release.

### Added

- Automatic detection of iPhone and Android phones over USB (Windows Portable Devices / MTP), with setup hints for locked or untrusted phones.
- Incremental backup of photos and videos, matched by name, size, and the phone's date or unique file ID.
- Duplicate detection by content hash, across phones.
- Configurable folder organisation patterns, with a live preview.
- Capture dates from EXIF (JPEG, HEIC) and MediaInfo (MOV, MP4), with the phone's file date as a fallback.
- Live Photo pairs (HEIC + MOV) kept together.
- Safe copying through a staging folder, with progress, speed, time remaining and cancellation.
- Device diagnostics report for troubleshooting.
