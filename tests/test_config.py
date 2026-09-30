from pathlib import Path

from photobackup.config import DEFAULT_PATTERN, Settings


def test_round_trip(tmp_path: Path):
    path = tmp_path / "settings.json"
    Settings(destination="D:/Backup", pattern="{type}/{filename}", include_videos=False).save(path)
    loaded = Settings.load(path)
    assert (loaded.destination, loaded.pattern, loaded.include_videos) == ("D:/Backup", "{type}/{filename}", False)


def test_missing_or_corrupt_file_gives_defaults(tmp_path: Path):
    assert Settings.load(tmp_path / "missing.json").pattern == DEFAULT_PATTERN
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert Settings.load(bad).destination


def test_unknown_keys_are_ignored(tmp_path: Path):
    path = tmp_path / "s.json"
    path.write_text('{"pattern": "{filename}", "future_option": 1}', encoding="utf-8")
    assert Settings.load(path).pattern == "{filename}"
