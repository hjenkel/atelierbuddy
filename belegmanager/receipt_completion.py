from __future__ import annotations

from dataclasses import replace

from .config import settings
from .constants import COST_ALLOCATION_STATUS_DRAFT, COST_ALLOCATION_STATUS_POSTED
from .models import Receipt
from .schemas import AllocationInput, ReceiptCompletionResult, ReceiptSaveInput


class ReceiptCompletionService:
    VALID_DOCUMENT_TYPES = {"invoice", "credit_note"}

    def evaluate_receipt(self, receipt: Receipt) -> ReceiptCompletionResult:
        subcategory_type_ids: dict[int, int] = {}
        allocations: list[AllocationInput] = []
        for allocation in receipt.allocations:
            if allocation.cost_subcategory_id is not None and allocation.cost_subcategory is not None:
                subcategory_type_ids[allocation.cost_subcategory_id] = allocation.cost_subcategory.cost_type_id
            allocations.append(
                AllocationInput(
                    cost_type_id=allocation.cost_type_id,
                    cost_subcategory_id=allocation.cost_subcategory_id,
                    project_id=allocation.project_id,
                    cost_area_id=allocation.cost_area_id,
                    amount_cents=allocation.amount_cents,
                    position=allocation.position,
                )
            )

        snapshot = ReceiptSaveInput(
            doc_date=receipt.doc_date,
            supplier_id=receipt.supplier_id,
            amount_gross_cents=receipt.amount_gross_cents,
            vat_rate_percent=receipt.vat_rate_percent,
            amount_net_cents=receipt.amount_net_cents,
            notes=receipt.notes,
            document_type=receipt.document_type,
            allocations=allocations,
        )
        return self.evaluate_snapshot(snapshot, subcategory_type_ids=subcategory_type_ids)

    def evaluate_snapshot(
        self,
        snapshot: ReceiptSaveInput,
        *,
        subcategory_type_ids: dict[int, int] | None = None,
    ) -> ReceiptCompletionResult:
        missing: list[str] = []
        normalized_document_type = (snapshot.document_type or "invoice").strip().lower()
        if normalized_document_type not in self.VALID_DOCUMENT_TYPES:
            normalized_document_type = "invoice"

        if snapshot.doc_date is None:
            missing.append("Belegdatum")
        if snapshot.supplier_id is None:
            missing.append("Anbieter")
        if snapshot.amount_gross_cents is None:
            missing.append(f"Brutto ({settings.default_currency})")
        if snapshot.vat_rate_percent is None:
            missing.append("USt-Satz")
        if snapshot.amount_net_cents is None:
            missing.append(f"Netto ({settings.default_currency})")

        gross_cents = snapshot.amount_gross_cents
        if gross_cents is not None:
            if normalized_document_type == "invoice" and gross_cents < 0:
                missing.append("Brutto-Vorzeichen")
            if normalized_document_type == "credit_note" and gross_cents > 0:
                missing.append("Brutto-Vorzeichen")

        relevant_allocations = [allocation for allocation in snapshot.allocations if not self._is_empty_allocation(allocation)]
        if not relevant_allocations:
            missing.append("Kostenzuordnung")
        else:
            expected_sign = 0
            if gross_cents is not None:
                if gross_cents > 0:
                    expected_sign = 1
                elif gross_cents < 0:
                    expected_sign = -1

            total = 0
            for allocation in relevant_allocations:
                if allocation.cost_type_id is None:
                    missing.append("Kostenkategorie")
                if allocation.cost_subcategory_id is None:
                    missing.append("Unterkategorie")
                elif allocation.cost_type_id is not None and subcategory_type_ids:
                    parent_cost_type_id = subcategory_type_ids.get(allocation.cost_subcategory_id)
                    if parent_cost_type_id is not None and parent_cost_type_id != allocation.cost_type_id:
                        missing.append("Unterkategorie passt nicht zur Kostenkategorie")

                has_project = allocation.project_id is not None
                has_cost_area = allocation.cost_area_id is not None
                if not has_project and not has_cost_area:
                    missing.append("Kostenstelle")

                if allocation.amount_cents is None or allocation.amount_cents == 0:
                    missing.append("Zuordnungsbetrag")
                else:
                    total += allocation.amount_cents
                    if expected_sign > 0 and allocation.amount_cents < 0:
                        missing.append("Zuordnungsbetrag-Vorzeichen")
                    if expected_sign < 0 and allocation.amount_cents > 0:
                        missing.append("Zuordnungsbetrag-Vorzeichen")

            if gross_cents is not None and total != gross_cents:
                missing.append("Kostenzuordnungssumme")

        deduped_missing = list(dict.fromkeys(missing))
        is_complete = not deduped_missing
        return ReceiptCompletionResult(
            missing_fields=deduped_missing,
            is_complete=is_complete,
            allocation_status_to_persist=COST_ALLOCATION_STATUS_POSTED if is_complete else COST_ALLOCATION_STATUS_DRAFT,
        )

    def with_computed_net(self, snapshot: ReceiptSaveInput) -> ReceiptSaveInput:
        return replace(
            snapshot,
            amount_net_cents=self._compute_net_cents(snapshot.amount_gross_cents, snapshot.vat_rate_percent),
        )

    def _compute_net_cents(self, gross_cents: int | None, vat_rate_percent: float | None) -> int | None:
        if gross_cents is None or vat_rate_percent is None:
            return None
        from decimal import Decimal, ROUND_HALF_UP

        gross = Decimal(gross_cents)
        divisor = Decimal("1") + (Decimal(str(vat_rate_percent)) / Decimal("100"))
        net = (gross / divisor).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return int(net)

    def _is_empty_allocation(self, allocation: AllocationInput) -> bool:
        return (
            allocation.cost_type_id is None
            and allocation.cost_subcategory_id is None
            and allocation.project_id is None
            and allocation.cost_area_id is None
            and (allocation.amount_cents is None or allocation.amount_cents == 0)
        )


def receipt_completion_service() -> ReceiptCompletionService:
    return ReceiptCompletionService()
