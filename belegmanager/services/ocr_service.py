from __future__ import annotations

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageOps
import pypdfium2 as pdfium
from sqlmodel import Session

from ..config import settings
from ..db import engine
from ..fts import upsert_fts_row
from ..models import Receipt
from ..schemas import JobResult
from ..utils.date_parser import parse_document_date
from ..utils.storage import normalized_pdf_path, ocr_output_paths, thumbnail_path
from .thumbnail_service import ThumbnailService

try:
    import pillow_heif  # type: ignore
except ImportError:  # pragma: no cover
    pillow_heif = None
else:
    pillow_heif.register_heif_opener()


class OCRService:
    def __init__(self, ocr_languages: str = settings.ocr_languages) -> None:
        self.ocr_languages = ocr_languages
        self.thumbnail_service = ThumbnailService()
        self._available_languages: set[str] | None = None
        self._languages_checked = False

    def process_receipt(self, receipt_id: int) -> JobResult:
        archive_file: Path | None = None
        with Session(engine) as session:
            receipt = session.get(Receipt, receipt_id)
            if not receipt:
                return JobResult(receipt_id=receipt_id, success=False, message="Beleg nicht gefunden")
            if receipt.deleted_at is not None:
                return JobResult(receipt_id=receipt_id, success=False, message="Beleg liegt im Papierkorb")
            receipt.status = "running"
            receipt.error_message = None
            receipt.updated_at = datetime.now(timezone.utc)
            session.add(receipt)
            session.commit()
            archive_file = Path(receipt.archive_path)

        if archive_file is None:
            return self._mark_error(receipt_id, "Archivpfad fehlt")

        try:
            source_pdf = self._normalize_for_ocr(archive_file, receipt_id)
            ocr_pdf, sidecar_txt = ocr_output_paths(receipt_id)
            ocr_pdf.parent.mkdir(parents=True, exist_ok=True)

            language_warning = self._run_ocr(source_pdf, ocr_pdf, sidecar_txt)

            ocr_text = sidecar_txt.read_text(encoding="utf-8", errors="ignore") if sidecar_txt.exists() else ""
            text_layer_warning = None
            if archive_file.suffix.lower() == ".pdf" and self._is_skip_placeholder_text(ocr_text):
                extracted_pdf_text = self._extract_text_from_pdf(archive_file)
                if extracted_pdf_text.strip():
                    ocr_text = extracted_pdf_text
                    text_layer_warning = (
                        "Hinweis: OCR wurde fuer ein textbasiertes PDF uebersprungen; "
                        "Text wurde direkt aus dem PDF-Textlayer uebernommen."
                    )
            proposed_date = parse_document_date(ocr_text)

            thumb_file = thumbnail_path(receipt_id)
            thumb_source = ocr_pdf if ocr_pdf.exists() else source_pdf
            try:
                self.thumbnail_service.generate(thumb_source, thumb_file)
                thumb_value = str(thumb_file)
            except Exception:
                thumb_value = None

            with Session(engine) as session:
                receipt = session.get(Receipt, receipt_id)
                if not receipt:
                    return JobResult(receipt_id=receipt_id, success=False, message="Beleg nach OCR nicht gefunden")
                if receipt.deleted_at is not None:
                    return JobResult(receipt_id=receipt_id, success=False, message="Beleg liegt im Papierkorb")

                receipt.ocr_pdf_path = str(ocr_pdf) if ocr_pdf.exists() else None
                receipt.ocr_text = ocr_text
                receipt.thumbnail_path = thumb_value
                if proposed_date and receipt.doc_date is None:
                    receipt.doc_date = proposed_date
                receipt.status = "done"
                info_messages = [message for message in (language_warning, text_layer_warning) if message]
                info_text = " ".join(info_messages).strip()
                receipt.error_message = info_text[:1000] if info_text else None
                receipt.updated_at = datetime.now(timezone.utc)

                session.add(receipt)
                upsert_fts_row(session, receipt_id=receipt_id, content=ocr_text)
                session.commit()

            return JobResult(receipt_id=receipt_id, success=True, message=info_text or "OCR abgeschlossen")
        except Exception as exc:  # pragma: no cover
            return self._mark_error(receipt_id, str(exc))

    def _run_ocr(self, source_pdf: Path, target_pdf: Path, sidecar_txt: Path) -> str | None:
        language_value, language_warning = self._resolve_ocr_languages()
        cmd = [
            "ocrmypdf",
            "--skip-text",
            "--language",
            language_value,
            "--sidecar",
            str(sidecar_txt),
            str(source_pdf),
            str(target_pdf),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except FileNotFoundError as exc:
            raise RuntimeError("ocrmypdf ist nicht installiert oder nicht im PATH") from exc
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            stdout = (exc.stdout or "").strip()
            message = stderr or stdout or "OCR fehlgeschlagen"
            raise RuntimeError(message) from exc
        return language_warning

    def _resolve_ocr_languages(self) -> tuple[str, str | None]:
        configured = [lang.strip() for lang in self.ocr_languages.split("+") if lang.strip()]
        if not configured:
            configured = ["eng"]

        available = self._detect_available_tesseract_languages()
        if not available:
            return "+".join(configured), None

        selected = [lang for lang in configured if lang in available]
        missing = [lang for lang in configured if lang not in available]

        if not selected:
            fallback = "eng" if "eng" in available else sorted(available)[0]
            warning = (
                f"Hinweis: OCR-Sprache(n) {', '.join(configured)} nicht installiert. "
                f"Verwende Fallback '{fallback}'. {self._language_setup_hint(configured)}"
            ).strip()
            return fallback, warning

        if missing:
            warning = (
                f"Hinweis: OCR-Sprache(n) {', '.join(missing)} nicht installiert. "
                f"Verwende {', '.join(selected)}. {self._language_setup_hint(missing)}"
            ).strip()
            return "+".join(selected), warning

        return "+".join(selected), None

    def _language_setup_hint(self, missing_languages: list[str]) -> str:
        if "deu" in missing_languages:
            return "macOS Setup: 'brew install tesseract-lang' ausfuehren."
        return ""

    def _detect_available_tesseract_languages(self) -> set[str] | None:
        if self._languages_checked:
            return self._available_languages

        self._languages_checked = True
        try:
            result = subprocess.run(
                ["tesseract", "--list-langs"],
                check=True,
                capture_output=True,
                text=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            self._available_languages = None
            return None

        available: set[str] = set()
        for line in result.stdout.splitlines():
            entry = line.strip()
            if not entry:
                continue
            if entry.lower().startswith("list of available languages"):
                continue
            available.add(entry)

        self._available_languages = available if available else None
        return self._available_languages

    def _normalize_for_ocr(self, source_file: Path, receipt_id: int) -> Path:
        if source_file.suffix.lower() == ".pdf":
            return source_file

        input_pdf = normalized_pdf_path(receipt_id)
        input_pdf.parent.mkdir(parents=True, exist_ok=True)

        with Image.open(source_file) as image:
            image = ImageOps.exif_transpose(image)
            rgb = image.convert("RGB")
            rgb.save(input_pdf, format="PDF")

        return input_pdf

    def _is_skip_placeholder_text(self, text: str) -> bool:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return False
        skip_pattern = re.compile(r"^\[ocr skipped on page\(s\).*\]$", re.IGNORECASE)
        return all(skip_pattern.match(line) for line in lines)

    def _extract_text_from_pdf(self, pdf_path: Path) -> str:
        document = pdfium.PdfDocument(str(pdf_path))
        fragments: list[str] = []
        try:
            for page_index in range(len(document)):
                page = document[page_index]
                try:
                    text_page = page.get_textpage()
                    try:
                        page_text = text_page.get_text_range() or ""
                    finally:
                        text_page.close()
                finally:
                    page.close()

                cleaned = page_text.strip()
                if cleaned:
                    fragments.append(cleaned)
        finally:
            document.close()

        return "\n\n".join(fragments)

    def _mark_error(self, receipt_id: int, message: str) -> JobResult:
        with Session(engine) as session:
            receipt = session.get(Receipt, receipt_id)
            if receipt:
                if receipt.deleted_at is not None:
                    return JobResult(receipt_id=receipt_id, success=False, message=message)
                receipt.status = "error"
                receipt.error_message = message[:1000]
                receipt.updated_at = datetime.now(timezone.utc)
                session.add(receipt)
                session.commit()
        return JobResult(receipt_id=receipt_id, success=False, message=message)
