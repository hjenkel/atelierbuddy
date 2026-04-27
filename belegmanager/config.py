from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


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
    order_invoices_dir: Path
    invoice_assets_dir: Path
    invoice_logos_dir: Path
    custom_invoice_template_dir: Path
    custom_invoice_fonts_dir: Path
    works_cover_dir: Path
    app_host: str = "127.0.0.1"
    app_port: int = 8080
    ocr_languages: str = "deu+eng"
    default_currency: str = "EUR"
    default_vat_rate_percent: float = 19.0
    session_secret: str = ""
    allowed_hosts: tuple[str, ...] = ("*",)
    allowed_origins: tuple[str, ...] = ()
    session_idle_minutes: int = 8 * 60
    session_max_age_hours: int = 7 * 24
    secure_cookies: str = "auto"
    max_upload_mb: int = 25
    ocr_timeout_seconds: int = 300

    def ensure_dirs(self) -> None:
        for directory in (
            self.data_dir,
            self.assets_dir,
            self.archive_dir,
            self.originals_dir,
            self.normalized_dir,
            self.ocr_dir,
            self.thumbs_dir,
            self.order_invoices_dir,
            self.invoice_assets_dir,
            self.invoice_logos_dir,
            self.custom_invoice_template_dir,
            self.custom_invoice_fonts_dir,
            self.works_cover_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    @property
    def max_upload_bytes(self) -> int:
        return int(self.max_upload_mb) * 1024 * 1024


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


def _env_bool_like_mode(name: str, default: str = "auto") -> str:
    value = _env_str(name, default).lower()
    if value in {"1", "true", "yes", "on"}:
        return "true"
    if value in {"0", "false", "no", "off"}:
        return "false"
    if value == "auto":
        return "auto"
    return default


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
    order_invoices_dir=DATA_DIR / "archive" / "order_invoices",
    invoice_assets_dir=DATA_DIR / "archive" / "invoice_assets",
    invoice_logos_dir=DATA_DIR / "archive" / "invoice_assets" / "logos",
    custom_invoice_template_dir=DATA_DIR / "invoice_templates" / "custom",
    custom_invoice_fonts_dir=DATA_DIR / "invoice_templates" / "custom" / "fonts",
    works_cover_dir=DATA_DIR / "archive" / "work_covers",
    app_host=_env_str("BM_HOST", "127.0.0.1"),
    app_port=_env_int("BM_PORT", 8080),
    ocr_languages=_env_str("BM_OCR_LANGUAGES", "deu+eng"),
    session_secret=_env_str("BM_SESSION_SECRET", ""),
    allowed_hosts=_split_csv(_env_str("BM_ALLOWED_HOSTS", "*")),
    allowed_origins=_split_csv(_env_str("BM_ALLOWED_ORIGINS", "")),
    session_idle_minutes=_env_int("BM_SESSION_IDLE_MINUTES", 8 * 60),
    session_max_age_hours=_env_int("BM_SESSION_MAX_AGE_HOURS", 7 * 24),
    secure_cookies=_env_bool_like_mode("BM_SECURE_COOKIES", "auto"),
    max_upload_mb=_env_int("BM_MAX_UPLOAD_MB", 25),
    ocr_timeout_seconds=_env_int("BM_OCR_TIMEOUT_SECONDS", 300),
)
