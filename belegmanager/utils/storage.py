from __future__ import annotations

import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image, ImageOps

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


def is_supported_receipt(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXTENSIONS


def is_supported_filename(name: str) -> bool:
    return Path(name).suffix.lower() in SUPPORTED_EXTENSIONS


def is_supported_image_filename(name: str) -> bool:
    return Path(name).suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS


def copy_to_archive(source: Path) -> Path:
    timestamp = datetime.now().strftime("%Y/%m")
    target_dir = settings.originals_dir / timestamp
    target_dir.mkdir(parents=True, exist_ok=True)

    unique_name = f"{uuid.uuid4().hex}{source.suffix.lower()}"
    destination = target_dir / unique_name
    shutil.copy2(source, destination)
    return destination


async def save_uploaded_to_archive(file_upload: "FileUpload") -> Path:
    suffix = Path(file_upload.name).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Nicht unterstützter Dateityp: {file_upload.name}")

    timestamp = datetime.now().strftime("%Y/%m")
    target_dir = settings.originals_dir / timestamp
    target_dir.mkdir(parents=True, exist_ok=True)

    unique_name = f"{uuid.uuid4().hex}{suffix}"
    destination = target_dir / unique_name
    await file_upload.save(destination)
    return destination


async def save_uploaded_work_cover(file_upload: "FileUpload", work_id: int) -> Path:
    suffix = Path(file_upload.name).suffix.lower()
    if suffix not in SUPPORTED_IMAGE_EXTENSIONS:
        raise ValueError(f"Nicht unterstützter Bildtyp: {file_upload.name}")

    target_dir = settings.works_cover_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    upload_token = uuid.uuid4().hex
    source_tmp = target_dir / f"work_{work_id}_{upload_token}{suffix}"
    destination = target_dir / f"work_{work_id}_{upload_token}.webp"
    await file_upload.save(source_tmp)

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
        rel = p.resolve().relative_to(settings.data_dir.resolve())
    except ValueError:
        return None
    return f"/files/{rel.as_posix()}"


def safe_delete_file(path: str | Path | None) -> None:
    if not path:
        return
    candidate = Path(path)
    try:
        resolved = candidate.resolve()
        resolved.relative_to(settings.data_dir.resolve())
    except (ValueError, FileNotFoundError):
        return
    resolved.unlink(missing_ok=True)
