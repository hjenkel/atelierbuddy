from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import delete
from sqlmodel import Session

from ..config import settings
from ..db import engine
from ..fts import delete_fts_row
from ..models import CostAllocation, Receipt, Supplier
from ..utils.storage import normalized_pdf_path, ocr_output_paths, safe_delete_file


class ReceiptService:
    VALID_DOCUMENT_TYPES = {"invoice", "credit_note"}

    def update_metadata(
        self,
        receipt_id: int,
        doc_date: date | None,
        supplier_id: int | None = None,
        amount_gross_cents: int | None = None,
        vat_rate_percent: float | None = None,
        notes: str | None = None,
        document_type: str = "invoice",
    ) -> None:
        with Session(engine) as session:
            receipt = session.get(Receipt, receipt_id)
            if not receipt or receipt.deleted_at is not None:
                raise ValueError("Beleg nicht gefunden")

            normalized_document_type = (document_type or "invoice").strip().lower()
            if normalized_document_type not in self.VALID_DOCUMENT_TYPES:
                raise ValueError("Ungültiger Belegtyp")

            receipt.doc_date = doc_date
            normalized_notes = (notes or "").strip()
            receipt.notes = normalized_notes or None
            if supplier_id is not None:
                supplier = session.get(Supplier, supplier_id)
                if supplier is None:
                    raise ValueError("Anbieter nicht gefunden")
            receipt.supplier_id = supplier_id
            receipt.document_type = normalized_document_type
            if amount_gross_cents is None:
                receipt.amount_gross_cents = None
                receipt.vat_rate_percent = None
                receipt.amount_net_cents = None
            else:
                if normalized_document_type == "invoice" and amount_gross_cents < 0:
                    raise ValueError("Bei Rechnung muss der Bruttobetrag >= 0 sein")
                if normalized_document_type == "credit_note" and amount_gross_cents > 0:
                    raise ValueError("Bei Gutschrift muss der Bruttobetrag <= 0 sein")
                effective_vat = vat_rate_percent
                if effective_vat is None:
                    effective_vat = settings.default_vat_rate_percent
                if effective_vat < 0:
                    raise ValueError("USt-Satz darf nicht negativ sein")

                receipt.amount_gross_cents = amount_gross_cents
                receipt.vat_rate_percent = float(effective_vat)
                receipt.amount_net_cents = self._calculate_net_cents(
                    gross_cents=amount_gross_cents,
                    vat_rate_percent=effective_vat,
                )
            receipt.updated_at = datetime.now(timezone.utc)
            session.add(receipt)
            session.commit()

    def _calculate_net_cents(self, gross_cents: int, vat_rate_percent: float) -> int:
        gross = Decimal(gross_cents)
        divisor = Decimal("1") + (Decimal(str(vat_rate_percent)) / Decimal("100"))
        net = (gross / divisor).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return int(net)

    def move_to_trash(self, receipt_id: int) -> None:
        with Session(engine) as session:
            receipt = session.get(Receipt, receipt_id)
            if not receipt:
                raise ValueError("Beleg nicht gefunden")
            if receipt.deleted_at is not None:
                return

            receipt.deleted_at = datetime.now(timezone.utc)
            receipt.updated_at = datetime.now(timezone.utc)
            session.add(receipt)
            session.commit()

    def restore_from_trash(self, receipt_id: int) -> None:
        with Session(engine) as session:
            receipt = session.get(Receipt, receipt_id)
            if not receipt:
                raise ValueError("Beleg nicht gefunden")

            receipt.deleted_at = None
            receipt.updated_at = datetime.now(timezone.utc)
            session.add(receipt)
            session.commit()

    def hard_delete(self, receipt_id: int) -> None:
        with Session(engine) as session:
            receipt = session.get(Receipt, receipt_id)
            if not receipt:
                raise ValueError("Beleg nicht gefunden")

            file_candidates = [
                receipt.archive_path,
                receipt.ocr_pdf_path,
                receipt.thumbnail_path,
            ]
            ocr_pdf_path, ocr_text_path = ocr_output_paths(receipt_id)
            file_candidates.append(ocr_pdf_path)
            file_candidates.append(ocr_text_path)
            file_candidates.append(normalized_pdf_path(receipt_id))

            session.exec(delete(CostAllocation).where(CostAllocation.receipt_id == receipt_id))
            delete_fts_row(session, receipt_id)
            session.delete(receipt)
            session.commit()

        for file_path in file_candidates:
            safe_delete_file(file_path)
