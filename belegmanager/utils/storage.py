from __future__ import annotations

import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image, ImageOps, UnidentifiedImageError

from ..config import settings

if TYPE_CHECKING:
    from nicegui.elements.upload_files import FileUpload

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:
    pass

SUPPORTED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".heic", ".heif"}
SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif"}
MAX_FILENAME_LENGTH = 180


def is_supported_receipt(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXTENSIONS


def is_supported_filename(name: str) -> bool:
    return Path(name).suffix.lower() in SUPPORTED_EXTENSIONS


def is_supported_image_filename(name: str) -> bool:
    return Path(name).suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS


def _ensure_safe_filename(name: str) -> None:
    filename = Path(name or "").name
    if not filename:
        raise ValueError("Dateiname fehlt")
    if len(filename) > MAX_FILENAME_LENGTH:
        raise ValueError(f"Dateiname zu lang (max. {MAX_FILENAME_LENGTH} Zeichen)")


def _ensure_max_size(path: Path) -> None:
    max_size = int(settings.max_upload_bytes)
    size = path.stat().st_size
    if size > max_size:
        raise ValueError(f"Datei zu groß (max. {settings.max_upload_mb} MB)")


def _ensure_valid_pdf(path: Path) -> None:
    with path.open("rb") as file_handle:
        header = file_handle.read(5)
    if header != b"%PDF-":
        raise ValueError("Ungültiger PDF-Inhalt")


def _ensure_valid_image(path: Path) -> None:
    try:
        with Image.open(path) as image:
            image.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Ungültiger Bildinhalt") from exc


def validate_receipt_file(path: Path) -> None:
    if not path.exists() or not path.is_file():
        raise ValueError("Datei nicht gefunden")
    if path.is_symlink():
        raise ValueError("Symlink-Dateien sind nicht erlaubt")
    _ensure_safe_filename(path.name)
    if not is_supported_receipt(path):
        raise ValueError("Dateityp nicht unterstützt")
    _ensure_max_size(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        _ensure_valid_pdf(path)
    else:
        _ensure_valid_image(path)


def validate_cover_file(path: Path) -> None:
    if not path.exists() or not path.is_file():
        raise ValueError("Datei nicht gefunden")
    if path.is_symlink():
        raise ValueError("Symlink-Dateien sind nicht erlaubt")
    _ensure_safe_filename(path.name)
    _ensure_max_size(path)
    if path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
        raise ValueError("Bildtyp nicht unterstützt")
    _ensure_valid_image(path)


def copy_to_archive(source: Path) -> Path:
    validate_receipt_file(source)
    timestamp = datetime.now().strftime("%Y/%m")
    target_dir = settings.originals_dir / timestamp
    target_dir.mkdir(parents=True, exist_ok=True)

    unique_name = f"{uuid.uuid4().hex}{source.suffix.lower()}"
    destination = target_dir / unique_name
    shutil.copy2(source, destination)
    return destination


async def save_uploaded_to_archive(file_upload: "FileUpload") -> Path:
    _ensure_safe_filename(file_upload.name)
    suffix = Path(file_upload.name).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Nicht unterstützter Dateityp: {file_upload.name}")
    try:
        upload_size = int(file_upload.size())
    except Exception:
        upload_size = 0
    if upload_size > int(settings.max_upload_bytes):
        raise ValueError(f"Datei zu groß (max. {settings.max_upload_mb} MB)")

    timestamp = datetime.now().strftime("%Y/%m")
    target_dir = settings.originals_dir / timestamp
    target_dir.mkdir(parents=True, exist_ok=True)

    unique_name = f"{uuid.uuid4().hex}{suffix}"
    destination = target_dir / unique_name
    await file_upload.save(destination)
    try:
        validate_receipt_file(destination)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination


async def save_uploaded_work_cover(file_upload: "FileUpload", work_id: int) -> Path:
    _ensure_safe_filename(file_upload.name)
    suffix = Path(file_upload.name).suffix.lower()
    if suffix not in SUPPORTED_IMAGE_EXTENSIONS:
        raise ValueError(f"Nicht unterstützter Bildtyp: {file_upload.name}")
    try:
        upload_size = int(file_upload.size())
    except Exception:
        upload_size = 0
    if upload_size > int(settings.max_upload_bytes):
        raise ValueError(f"Datei zu groß (max. {settings.max_upload_mb} MB)")

    target_dir = settings.works_cover_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    upload_token = uuid.uuid4().hex
    source_tmp = target_dir / f"work_{work_id}_{upload_token}{suffix}"
    destination = target_dir / f"work_{work_id}_{upload_token}.webp"
    await file_upload.save(source_tmp)
    validate_cover_file(source_tmp)

    try:
        with Image.open(source_tmp) as image:
            image = ImageOps.exif_transpose(image)
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGB")
            if image.mode == "RGBA":
                flattened = Image.new("RGB", image.size, "#ffffff")
                flattened.paste(image, mask=image.split()[3])
                image = flattened

            image.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
            image.save(destination, format="WEBP", quality=84, method=6)
    finally:
        source_tmp.unlink(missing_ok=True)

    return destination


def ocr_output_paths(receipt_id: int) -> tuple[Path, Path]:
    pdf_path = settings.ocr_dir / f"receipt_{receipt_id}.pdf"
    txt_path = settings.ocr_dir / f"receipt_{receipt_id}.txt"
    return pdf_path, txt_path


def normalized_pdf_path(receipt_id: int) -> Path:
    return settings.normalized_dir / f"receipt_{receipt_id}_input.pdf"


def thumbnail_path(receipt_id: int) -> Path:
    return settings.thumbs_dir / f"receipt_{receipt_id}.jpg"


def to_files_url(path: str | Path | None) -> str | None:
    if not path:
        return None
    p = Path(path)
    try:
        rel = p.resolve().relative_to(settings.archive_dir.resolve())
    except ValueError:
        return None
    return f"/files/{rel.as_posix()}"


def safe_delete_file(path: str | Path | None) -> None:
    if not path:
        return
    candidate = Path(path)
    try:
        resolved = candidate.resolve()
        resolved.relative_to(settings.archive_dir.resolve())
    except (ValueError, FileNotFoundError):
        return
    resolved.unlink(missing_ok=True)
