# Contributing to PhotoBackup

Thanks for helping! Bug reports, device reports and pull requests are all welcome.

## Reporting a bug

Please [open an issue](../../issues/new/choose) using the bug report template, and include:

- your phone model and OS version (for example "Pixel 9, Android 16" or "iPhone 15, iOS 26");
- your Windows version and the PhotoBackup version (shown under **Help > About**);
- what you expected to happen and what happened instead.

If the problem involves a phone, attach a diagnostics report: select the phone and click **Save diagnostics...**. The report contains file and folder names from the phone, so review it first and remove anything you don't want to share.

## Development setup

```powershell
git clone <your fork>
cd photobackup
python -m pip install -e ".[dev]"
python -m pytest
python run.py
```

The tests don't need a phone. Code that talks to devices (`photobackup/devices/wpd.py`) can only be fully tested with real hardware, so please say in your pull request which phones you tried.

## Pull requests

- Keep changes focused. One fix or feature per pull request is easiest to review.
- Add or update tests in `tests/` for behaviour changes.
- Make sure `python -m pytest` passes. Continuous integration runs it on Windows for every pull request.
- Match the style of the surrounding code.
- Add a line to the "Unreleased" section of `CHANGELOG.md` for any user-visible change.

By contributing, you agree that your contributions are licensed under the project's licence, GPL-3.0-or-later.

## Making a release (maintainers)

1. Update `__version__` in `photobackup/__init__.py` and move the "Unreleased" notes in `CHANGELOG.md` under the new version.
2. Commit, then tag and push: `git tag v0.2.0 && git push origin v0.2.0`.
3. The **Release** workflow builds the Windows zip and publishes it as a GitHub release.
