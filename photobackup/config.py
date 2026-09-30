from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from platformdirs import user_data_dir, user_pictures_dir

from photobackup import APP_NAME

DEFAULT_PATTERN = "{year}/{month:02} - {month_name}/{filename}"

DUPLICATE_SKIP = "skip"
DUPLICATE_KEEP_BOTH = "keep_both"


def app_data_dir() -> Path:
    path = Path(user_data_dir(APP_NAME, appauthor=False, roaming=True))
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_path() -> Path:
    return app_data_dir() / "settings.json"


def index_db_path() -> Path:
    return app_data_dir() / "index.db"


def _default_destination() -> str:
    return str(Path(user_pictures_dir()) / "Phone Backup")


@dataclass
class Settings:
    destination: str = ""
    pattern: str = DEFAULT_PATTERN
    include_photos: bool = True
    include_videos: bool = True
    scan_all_folders: bool = False
    duplicate_policy: str = DUPLICATE_SKIP
    poll_interval_seconds: int = 3

    def __post_init__(self) -> None:
        if not self.destination:
            self.destination = _default_destination()

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or settings_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: Path | None = None) -> None:
        path = path or settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
