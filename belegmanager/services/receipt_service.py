from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import delete
from sqlmodel import Session

from ..db import engine
from ..fts import delete_fts_row
from ..models import CostAllocation, Receipt, Supplier
from ..receipt_completion import ReceiptCompletionService
from ..schemas import ReceiptCompletionResult, ReceiptSaveInput
from .cost_allocation_service import CostAllocationService
from ..utils.storage import normalized_pdf_path, ocr_output_paths, safe_delete_file


class ReceiptService:
    VALID_DOCUMENT_TYPES = {"invoice", "credit_note"}

    def __init__(
        self,
        db_engine=engine,
        *,
        cost_allocation_service: CostAllocationService | None = None,
        completion_service: ReceiptCompletionService | None = None,
    ) -> None:
        self._engine = db_engine
        self._completion_service = completion_service or ReceiptCompletionService()
        self._cost_allocation_service = cost_allocation_service or CostAllocationService(
            db_engine=db_engine,
            completion_service=self._completion_service,
        )

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
        with Session(self._engine) as session:
            receipt = session.get(Receipt, receipt_id)
            if not receipt or receipt.deleted_at is not None:
                raise ValueError("Beleg nicht gefunden")
            self._apply_metadata(
                session,
                receipt=receipt,
                doc_date=doc_date,
                supplier_id=supplier_id,
                amount_gross_cents=amount_gross_cents,
                vat_rate_percent=vat_rate_percent,
                notes=notes,
                document_type=document_type,
            )
            session.commit()

    def save_detail(self, receipt_id: int, data: ReceiptSaveInput) -> ReceiptCompletionResult:
        with Session(self._engine) as session:
            receipt = session.get(Receipt, receipt_id)
            if not receipt or receipt.deleted_at is not None:
                raise ValueError("Beleg nicht gefunden")

            normalized_snapshot = self._apply_metadata(
                session,
                receipt=receipt,
                doc_date=data.doc_date,
                supplier_id=data.supplier_id,
                amount_gross_cents=data.amount_gross_cents,
                vat_rate_percent=data.vat_rate_percent,
                notes=data.notes,
                document_type=data.document_type,
            )
            normalized_allocations, subcategory_type_ids = self._cost_allocation_service.prepare_allocations(
                session,
                data.allocations,
            )
            normalized_snapshot = ReceiptSaveInput(
                doc_date=normalized_snapshot.doc_date,
                supplier_id=normalized_snapshot.supplier_id,
                amount_gross_cents=normalized_snapshot.amount_gross_cents,
                vat_rate_percent=normalized_snapshot.vat_rate_percent,
                amount_net_cents=normalized_snapshot.amount_net_cents,
                notes=normalized_snapshot.notes,
                document_type=normalized_snapshot.document_type,
                allocations=normalized_allocations,
            )
            completion = self._completion_service.evaluate_snapshot(
                normalized_snapshot,
                subcategory_type_ids=subcategory_type_ids,
            )
            if completion.is_complete:
                self._cost_allocation_service._validate_allocations_payload(
                    normalized_allocations,
                    normalized_snapshot.amount_gross_cents or 0,
                    document_type=normalized_snapshot.document_type,
                    subcategory_type_ids=subcategory_type_ids,
                )
            self._cost_allocation_service.replace_allocations(
                session,
                receipt_id,
                normalized_allocations,
                allocation_status=completion.allocation_status_to_persist,
            )
            receipt.updated_at = datetime.now(timezone.utc)
            session.add(receipt)
            session.commit()
            return completion

    def evaluate_snapshot(self, data: ReceiptSaveInput) -> ReceiptCompletionResult:
        normalized_vat = data.vat_rate_percent
        if data.amount_gross_cents is None:
            normalized_vat = None
        normalized_snapshot = ReceiptSaveInput(
            doc_date=data.doc_date,
            supplier_id=data.supplier_id,
            amount_gross_cents=data.amount_gross_cents,
            vat_rate_percent=normalized_vat,
            amount_net_cents=self._compute_net_cents(data.amount_gross_cents, normalized_vat),
            notes=data.notes,
            document_type=self._normalize_document_type(data.document_type),
            allocations=data.allocations,
        )
        return self._completion_service.evaluate_snapshot(normalized_snapshot)

    def _apply_metadata(
        self,
        session: Session,
        *,
        receipt: Receipt,
        doc_date: date | None,
        supplier_id: int | None,
        amount_gross_cents: int | None,
        vat_rate_percent: float | None,
        notes: str | None,
        document_type: str,
    ) -> ReceiptSaveInput:
        normalized_document_type = self._normalize_document_type(document_type)
        normalized_notes = (notes or "").strip() or None
        if supplier_id is not None:
            supplier = session.get(Supplier, supplier_id)
            if supplier is None:
                raise ValueError("Anbieter nicht gefunden")

        if amount_gross_cents is not None:
            if normalized_document_type == "invoice" and amount_gross_cents < 0:
                raise ValueError("Bei Rechnung muss der Bruttobetrag >= 0 sein")
            if normalized_document_type == "credit_note" and amount_gross_cents > 0:
                raise ValueError("Bei Gutschrift muss der Bruttobetrag <= 0 sein")

        effective_vat = vat_rate_percent
        if amount_gross_cents is None:
            effective_vat = None
        if effective_vat is not None and effective_vat < 0:
            raise ValueError("USt-Satz darf nicht negativ sein")

        computed_net_cents = self._compute_net_cents(amount_gross_cents, effective_vat)

        receipt.doc_date = doc_date
        receipt.notes = normalized_notes
        receipt.supplier_id = supplier_id
        receipt.document_type = normalized_document_type
        receipt.amount_gross_cents = amount_gross_cents
        receipt.vat_rate_percent = float(effective_vat) if effective_vat is not None else None
        receipt.amount_net_cents = computed_net_cents
        receipt.updated_at = datetime.now(timezone.utc)
        session.add(receipt)

        return ReceiptSaveInput(
            doc_date=doc_date,
            supplier_id=supplier_id,
            amount_gross_cents=amount_gross_cents,
            vat_rate_percent=float(effective_vat) if effective_vat is not None else None,
            amount_net_cents=computed_net_cents,
            notes=normalized_notes,
            document_type=normalized_document_type,
            allocations=[],
        )

    def _calculate_net_cents(self, gross_cents: int, vat_rate_percent: float) -> int:
        gross = Decimal(gross_cents)
        divisor = Decimal("1") + (Decimal(str(vat_rate_percent)) / Decimal("100"))
        net = (gross / divisor).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return int(net)

    def _compute_net_cents(self, gross_cents: int | None, vat_rate_percent: float | None) -> int | None:
        if gross_cents is None or vat_rate_percent is None:
            return None
        return self._calculate_net_cents(gross_cents, vat_rate_percent)

    def _normalize_document_type(self, document_type: str) -> str:
        normalized_document_type = (document_type or "invoice").strip().lower()
        if normalized_document_type not in self.VALID_DOCUMENT_TYPES:
            raise ValueError("Ungültiger Belegtyp")
        return normalized_document_type

    def move_to_trash(self, receipt_id: int) -> None:
        with Session(self._engine) as session:
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
        with Session(self._engine) as session:
            receipt = session.get(Receipt, receipt_id)
            if not receipt:
                raise ValueError("Beleg nicht gefunden")

            receipt.deleted_at = None
            receipt.updated_at = datetime.now(timezone.utc)
            session.add(receipt)
            session.commit()

    def hard_delete(self, receipt_id: int) -> None:
        with Session(self._engine) as session:
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
