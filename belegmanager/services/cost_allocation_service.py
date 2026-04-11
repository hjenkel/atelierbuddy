from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete
from sqlmodel import Session, select

from ..config import settings
from ..constants import DEFAULT_HIDDEN_COST_AREA_NAME
from ..db import engine
from ..models import CostAllocation, CostArea, CostSubcategory, CostType, Project, Receipt
from ..schemas import AllocationInput


class CostAllocationService:
    VALID_DOCUMENT_TYPES = {"invoice", "credit_note"}

    def save_allocations(self, receipt_id: int, allocations: list[AllocationInput]) -> None:
        with Session(engine) as session:
            receipt = session.get(Receipt, receipt_id)
            if not receipt or receipt.deleted_at is not None:
                raise ValueError("Beleg nicht gefunden")

            gross_cents = receipt.amount_gross_cents
            if gross_cents is None:
                raise ValueError("Bruttobetrag fehlt")

            document_type = (receipt.document_type or "invoice").strip().lower()
            if document_type not in self.VALID_DOCUMENT_TYPES:
                document_type = "invoice"

            self._validate_allocations_payload(allocations, gross_cents, document_type=document_type)

            default_cost_area = session.exec(
                select(CostArea).where(CostArea.name == DEFAULT_HIDDEN_COST_AREA_NAME)
            ).first()
            if not default_cost_area or default_cost_area.id is None:
                raise ValueError("Standard-Kostenstelle fehlt")

            normalized_allocations: list[AllocationInput] = []
            for item in allocations:
                normalized_cost_area_id = item.cost_area_id
                if item.project_id is None and normalized_cost_area_id is None:
                    normalized_cost_area_id = default_cost_area.id
                normalized_allocations.append(
                    AllocationInput(
                        cost_type_id=item.cost_type_id,
                        cost_subcategory_id=item.cost_subcategory_id,
                        project_id=item.project_id,
                        cost_area_id=normalized_cost_area_id,
                        amount_cents=item.amount_cents,
                        position=item.position,
                    )
                )

            cost_type_ids = sorted({item.cost_type_id for item in normalized_allocations})
            cost_subcategory_ids = sorted(
                {item.cost_subcategory_id for item in normalized_allocations if item.cost_subcategory_id is not None}
            )
            project_ids = sorted({item.project_id for item in normalized_allocations if item.project_id is not None})
            cost_area_ids = sorted({item.cost_area_id for item in normalized_allocations if item.cost_area_id is not None})

            existing_cost_types = {
                item.id for item in session.exec(select(CostType).where(CostType.id.in_(cost_type_ids))).all() if item.id is not None
            }
            if len(existing_cost_types) != len(cost_type_ids):
                raise ValueError("Mindestens eine Kostenkategorie ist ungültig")

            existing_subcategories = {
                item.id: item
                for item in session.exec(select(CostSubcategory).where(CostSubcategory.id.in_(cost_subcategory_ids))).all()
                if item.id is not None
            }
            if len(existing_subcategories) != len(cost_subcategory_ids):
                raise ValueError("Mindestens eine Unterkategorie ist ungültig")

            if project_ids:
                existing_projects = {
                    item.id for item in session.exec(select(Project).where(Project.id.in_(project_ids))).all() if item.id is not None
                }
                if len(existing_projects) != len(project_ids):
                    raise ValueError("Mindestens ein Projekt ist ungültig")

            if cost_area_ids:
                existing_cost_areas = {
                    item.id
                    for item in session.exec(select(CostArea).where(CostArea.id.in_(cost_area_ids))).all()
                    if item.id is not None
                }
                if len(existing_cost_areas) != len(cost_area_ids):
                    raise ValueError("Mindestens eine Kostenstelle ist ungültig")

            for item in normalized_allocations:
                subcategory = existing_subcategories.get(item.cost_subcategory_id)
                if subcategory is None:
                    raise ValueError("Unterkategorie fehlt")
                if subcategory.cost_type_id != item.cost_type_id:
                    raise ValueError("Unterkategorie passt nicht zur Kostenkategorie")

            session.exec(delete(CostAllocation).where(CostAllocation.receipt_id == receipt_id))

            now = datetime.now(timezone.utc)
            for index, item in enumerate(normalized_allocations):
                session.add(
                    CostAllocation(
                        receipt_id=receipt_id,
                        cost_type_id=item.cost_type_id,
                        cost_subcategory_id=item.cost_subcategory_id,
                        project_id=item.project_id,
                        cost_area_id=item.cost_area_id,
                        amount_cents=item.amount_cents,
                        position=item.position if item.position > 0 else (index + 1),
                        created_at=now,
                        updated_at=now,
                    )
                )

            receipt.updated_at = now
            session.add(receipt)
            session.commit()

    def validate_for_receipt(self, receipt: Receipt) -> list[str]:
        missing: list[str] = []
        if not receipt.allocations:
            return ["Kostenzuordnung"]

        if receipt.amount_gross_cents is None:
            return [f"Brutto ({settings.default_currency})"]

        document_type = (receipt.document_type or "invoice").strip().lower()
        if document_type == "invoice" and receipt.amount_gross_cents < 0:
            missing.append("Brutto-Vorzeichen")
        if document_type == "credit_note" and receipt.amount_gross_cents > 0:
            missing.append("Brutto-Vorzeichen")

        total = 0
        expected_sign = 0
        if receipt.amount_gross_cents > 0:
            expected_sign = 1
        elif receipt.amount_gross_cents < 0:
            expected_sign = -1
        for allocation in receipt.allocations:
            if allocation.cost_type_id is None:
                missing.append("Kostenkategorie")
            if allocation.cost_subcategory_id is None:
                missing.append("Unterkategorie")
            elif allocation.cost_subcategory and allocation.cost_subcategory.cost_type_id != allocation.cost_type_id:
                missing.append("Unterkategorie passt nicht zur Kostenkategorie")
            has_project = allocation.project_id is not None
            has_cost_area = allocation.cost_area_id is not None
            if not has_project and not has_cost_area:
                missing.append("Kostenstelle")
            if allocation.amount_cents is None or allocation.amount_cents == 0:
                missing.append("Zuordnungsbetrag")
            else:
                if expected_sign > 0 and allocation.amount_cents < 0:
                    missing.append("Zuordnungsbetrag-Vorzeichen")
                if expected_sign < 0 and allocation.amount_cents > 0:
                    missing.append("Zuordnungsbetrag-Vorzeichen")
                if expected_sign == 0:
                    missing.append("Zuordnungsbetrag-Vorzeichen")
                total += allocation.amount_cents

        if total != receipt.amount_gross_cents:
            missing.append("Kostenzuordnungssumme")
        return sorted(set(missing))

    def _validate_allocations_payload(
        self,
        allocations: list[AllocationInput],
        gross_cents: int,
        *,
        document_type: str = "invoice",
    ) -> None:
        normalized_document_type = (document_type or "invoice").strip().lower()
        if normalized_document_type not in self.VALID_DOCUMENT_TYPES:
            raise ValueError("Ungültiger Belegtyp")
        if normalized_document_type == "invoice" and gross_cents < 0:
            raise ValueError("Bei Rechnung muss der Bruttobetrag >= 0 sein")
        if normalized_document_type == "credit_note" and gross_cents > 0:
            raise ValueError("Bei Gutschrift muss der Bruttobetrag <= 0 sein")
        if not allocations:
            raise ValueError("Mindestens eine Kostenzuordnung ist erforderlich")

        total = 0
        expected_sign = 0
        if gross_cents > 0:
            expected_sign = 1
        elif gross_cents < 0:
            expected_sign = -1
        else:
            raise ValueError("Bruttobetrag 0 kann nicht auf Zuordnungszeilen verteilt werden")
        for item in allocations:
            if item.cost_type_id <= 0:
                raise ValueError("Jede Kostenzuordnung braucht eine Kostenkategorie")
            if item.cost_subcategory_id <= 0:
                raise ValueError("Jede Kostenzuordnung braucht eine Unterkategorie")
            has_project = item.project_id is not None
            has_cost_area = item.cost_area_id is not None
            if has_project and has_cost_area:
                raise ValueError("Kostenzuordnung darf nicht gleichzeitig Projekt und Kostenstelle haben")
            if item.amount_cents == 0:
                raise ValueError("Zuordnungsbetrag darf nicht 0 sein")
            if expected_sign > 0 and item.amount_cents < 0:
                raise ValueError("Zuordnungsbetrag muss bei Rechnung positiv sein")
            if expected_sign < 0 and item.amount_cents > 0:
                raise ValueError("Zuordnungsbetrag muss bei Gutschrift negativ sein")
            total += item.amount_cents

        if total != gross_cents:
            raise ValueError("Kostenzuordnungen müssen die Belegsumme vollständig abdecken")
