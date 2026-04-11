from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from sqlmodel import Session

from ..db import engine
from ..models import ImportBatch, Receipt
from ..utils.storage import (
    copy_to_archive,
    is_supported_filename,
    is_supported_receipt,
    save_uploaded_to_archive,
)

if TYPE_CHECKING:
    from nicegui.elements.upload_files import FileUpload


class ImportService:
    def __init__(self, enqueue_job: Callable[[int], None]) -> None:
        self.enqueue_job = enqueue_job

    def import_folder(self, folder_path: str) -> ImportBatch:
        folder_path = folder_path.strip()
        if not folder_path:
            raise ValueError("Bitte einen Ordnerpfad angeben")

        source_dir = Path(folder_path).expanduser()
        if not source_dir.exists() or not source_dir.is_dir():
            raise ValueError("Der angegebene Ordner existiert nicht")
        source_dir_resolved = source_dir.resolve()

        files: list[Path] = []
        for path in source_dir.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            if not is_supported_receipt(path):
                continue
            try:
                path.resolve().relative_to(source_dir_resolved)
            except ValueError:
                continue
            files.append(path)
        files.sort()
        if not files:
            raise ValueError("Keine unterstützten Dateien gefunden")

        imported_receipt_ids: list[int] = []
        with Session(engine) as session:
            batch = ImportBatch(source_folder=str(source_dir), started_at=datetime.now(timezone.utc))
            session.add(batch)
            session.commit()
            session.refresh(batch)

            imported_count = 0
            error_count = 0
            for file_path in files:
                try:
                    file_path.resolve().relative_to(source_dir_resolved)
                    archived = copy_to_archive(file_path)
                    receipt = Receipt(
                        original_filename=file_path.name,
                        archive_path=str(archived),
                        status="queued",
                        import_batch_id=batch.id,
                    )
                    session.add(receipt)
                    session.commit()
                    session.refresh(receipt)
                    if receipt.id is not None:
                        imported_receipt_ids.append(receipt.id)
                    imported_count += 1
                except Exception:
                    error_count += 1

            batch.total_count = len(files)
            batch.imported_count = imported_count
            batch.error_count = error_count
            batch.finished_at = datetime.now(timezone.utc)
            session.add(batch)
            session.commit()
            session.refresh(batch)

        for receipt_id in imported_receipt_ids:
            self.enqueue_job(receipt_id)

        return batch

    async def import_uploaded_files(self, files: list[FileUpload], source_label: str = "Browser Upload") -> ImportBatch:
        supported_files = [file for file in files if is_supported_filename(file.name)]
        unsupported_count = len(files) - len(supported_files)

        if not supported_files:
            raise ValueError("Keine unterstützten Dateien ausgewählt (PDF/JPG/PNG/HEIC)")

        imported_receipt_ids: list[int] = []
        with Session(engine) as session:
            batch = ImportBatch(
                source_folder=((source_label or "").strip() or "Browser Upload")[:255],
                started_at=datetime.now(timezone.utc),
            )
            session.add(batch)
            session.commit()
            session.refresh(batch)

            imported_count = 0
            error_count = unsupported_count
            for file in supported_files:
                try:
                    archived = await save_uploaded_to_archive(file)
                    receipt = Receipt(
                        original_filename=file.name,
                        archive_path=str(archived),
                        status="queued",
                        import_batch_id=batch.id,
                    )
                    session.add(receipt)
                    session.commit()
                    session.refresh(receipt)
                    if receipt.id is not None:
                        imported_receipt_ids.append(receipt.id)
                    imported_count += 1
                except Exception:
                    error_count += 1

            batch.total_count = len(files)
            batch.imported_count = imported_count
            batch.error_count = error_count
            batch.finished_at = datetime.now(timezone.utc)
            session.add(batch)
            session.commit()
            session.refresh(batch)

        for receipt_id in imported_receipt_ids:
            self.enqueue_job(receipt_id)

        return batch
