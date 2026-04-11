from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    root_dir: Path
    data_dir: Path
    assets_dir: Path
    db_path: Path
    archive_dir: Path
    originals_dir: Path
    normalized_dir: Path
    ocr_dir: Path
    thumbs_dir: Path
    works_cover_dir: Path
    app_host: str = "127.0.0.1"
    app_port: int = 8080
    ocr_languages: str = "deu+eng"
    default_currency: str = "EUR"
    default_vat_rate_percent: float = 19.0

    def ensure_dirs(self) -> None:
        for directory in (
            self.data_dir,
            self.assets_dir,
            self.archive_dir,
            self.originals_dir,
            self.normalized_dir,
            self.ocr_dir,
            self.thumbs_dir,
            self.works_cover_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
ASSETS_DIR = ROOT_DIR / "assets"


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name, default).strip()
    return value or default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    return value if value > 0 else default


settings = Settings(
    root_dir=ROOT_DIR,
    data_dir=DATA_DIR,
    assets_dir=ASSETS_DIR,
    db_path=DATA_DIR / "belegmanager.db",
    archive_dir=DATA_DIR / "archive",
    originals_dir=DATA_DIR / "archive" / "originals",
    normalized_dir=DATA_DIR / "archive" / "normalized",
    ocr_dir=DATA_DIR / "archive" / "ocr",
    thumbs_dir=DATA_DIR / "archive" / "thumbs",
    works_cover_dir=DATA_DIR / "archive" / "work_covers",
    app_host=_env_str("BM_HOST", "127.0.0.1"),
    app_port=_env_int("BM_PORT", 8080),
    ocr_languages=_env_str("BM_OCR_LANGUAGES", "deu+eng"),
)
