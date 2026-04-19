from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete
from sqlmodel import Session, select

from ..constants import (
    COST_ALLOCATION_STATUS_POSTED,
    DEFAULT_HIDDEN_COST_AREA_NAME,
)
from ..db import engine
from ..models import CostAllocation, CostArea, CostSubcategory, CostType, Project, Receipt
from ..receipt_completion import ReceiptCompletionService
from ..schemas import AllocationInput


class CostAllocationService:
    VALID_DOCUMENT_TYPES = {"invoice", "credit_note"}

    def __init__(self, db_engine: Any = None, completion_service: ReceiptCompletionService | None = None) -> None:
        self._engine = db_engine or engine
        self._completion_service = completion_service or ReceiptCompletionService()

    def prepare_allocations(
        self,
        session: Session,
        allocations: list[AllocationInput],
    ) -> tuple[list[AllocationInput], dict[int, int]]:
        default_cost_area_id = self._default_cost_area_id(session)
        normalized: list[AllocationInput] = []
        for index, item in enumerate(allocations):
            normalized_item = AllocationInput(
                cost_type_id=self._to_optional_positive_int(item.cost_type_id),
                cost_subcategory_id=self._to_optional_positive_int(item.cost_subcategory_id),
                project_id=self._to_optional_positive_int(item.project_id),
                cost_area_id=self._to_optional_positive_int(item.cost_area_id),
                amount_cents=item.amount_cents,
                position=item.position if item.position > 0 else (index + 1),
            )
            if self._is_empty_allocation(normalized_item):
                continue
            if normalized_item.project_id is None and normalized_item.cost_area_id is None and default_cost_area_id is not None:
                normalized_item = replace(normalized_item, cost_area_id=default_cost_area_id)
            normalized.append(normalized_item)

        subcategory_type_ids = self._validate_reference_ids(session, normalized)
        return normalized, subcategory_type_ids

    def replace_allocations(
        self,
        session: Session,
        receipt_id: int,
        allocations: list[AllocationInput],
        *,
        allocation_status: str,
    ) -> None:
        session.exec(delete(CostAllocation).where(CostAllocation.receipt_id == receipt_id))
        now = datetime.now(timezone.utc)
        for index, item in enumerate(allocations):
            session.add(
                CostAllocation(
                    receipt_id=receipt_id,
                    cost_type_id=item.cost_type_id,
                    cost_subcategory_id=item.cost_subcategory_id,
                    project_id=item.project_id,
                    cost_area_id=item.cost_area_id,
                    amount_cents=int(item.amount_cents or 0),
                    position=item.position if item.position > 0 else (index + 1),
                    status=allocation_status,
                    created_at=now,
                    updated_at=now,
                )
            )

    def save_allocations(self, receipt_id: int, allocations: list[AllocationInput]) -> None:
        with Session(self._engine) as session:
            receipt = session.get(Receipt, receipt_id)
            if not receipt or receipt.deleted_at is not None:
                raise ValueError("Beleg nicht gefunden")

            normalized_allocations, subcategory_type_ids = self.prepare_allocations(session, allocations)
            gross_cents = receipt.amount_gross_cents
            if gross_cents is None:
                raise ValueError("Bruttobetrag fehlt")

            document_type = (receipt.document_type or "invoice").strip().lower()
            if document_type not in self.VALID_DOCUMENT_TYPES:
                document_type = "invoice"

            self._validate_allocations_payload(
                normalized_allocations,
                gross_cents,
                document_type=document_type,
                subcategory_type_ids=subcategory_type_ids,
            )
            self.replace_allocations(
                session,
                receipt_id,
                normalized_allocations,
                allocation_status=COST_ALLOCATION_STATUS_POSTED,
            )
            receipt.updated_at = datetime.now(timezone.utc)
            session.add(receipt)
            session.commit()

    def _validate_reference_ids(self, session: Session, allocations: list[AllocationInput]) -> dict[int, int]:
        cost_type_ids = sorted({item.cost_type_id for item in allocations if item.cost_type_id is not None})
        cost_subcategory_ids = sorted(
            {item.cost_subcategory_id for item in allocations if item.cost_subcategory_id is not None}
        )
        project_ids = sorted({item.project_id for item in allocations if item.project_id is not None})
        cost_area_ids = sorted({item.cost_area_id for item in allocations if item.cost_area_id is not None})

        if cost_type_ids:
            existing_cost_types = {
                item.id
                for item in session.exec(select(CostType).where(CostType.id.in_(cost_type_ids))).all()
                if item.id is not None
            }
            if len(existing_cost_types) != len(cost_type_ids):
                raise ValueError("Mindestens eine Kostenkategorie ist ungültig")

        existing_subcategories: dict[int, CostSubcategory] = {}
        if cost_subcategory_ids:
            existing_subcategories = {
                item.id: item
                for item in session.exec(select(CostSubcategory).where(CostSubcategory.id.in_(cost_subcategory_ids))).all()
                if item.id is not None
            }
            if len(existing_subcategories) != len(cost_subcategory_ids):
                raise ValueError("Mindestens eine Unterkategorie ist ungültig")

        if project_ids:
            existing_projects = {
                item.id
                for item in session.exec(select(Project).where(Project.id.in_(project_ids))).all()
                if item.id is not None
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

        return {
            item_id: subcategory.cost_type_id
            for item_id, subcategory in existing_subcategories.items()
        }

    def _validate_allocations_payload(
        self,
        allocations: list[AllocationInput],
        gross_cents: int,
        *,
        document_type: str = "invoice",
        subcategory_type_ids: dict[int, int] | None = None,
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
            if item.cost_type_id is None or item.cost_type_id <= 0:
                raise ValueError("Jede Kostenzuordnung braucht eine Kostenkategorie")
            if item.cost_subcategory_id is None or item.cost_subcategory_id <= 0:
                raise ValueError("Jede Kostenzuordnung braucht eine Unterkategorie")
            has_project = item.project_id is not None and item.project_id > 0
            has_cost_area = item.cost_area_id is not None and item.cost_area_id > 0
            if has_project and has_cost_area:
                raise ValueError("Kostenzuordnung darf nicht gleichzeitig Projekt und Kostenstelle haben")
            if not has_project and not has_cost_area:
                raise ValueError("Jede Kostenzuordnung braucht eine Kostenstelle")
            if item.amount_cents is None or item.amount_cents == 0:
                raise ValueError("Zuordnungsbetrag darf nicht 0 sein")
            if expected_sign > 0 and item.amount_cents < 0:
                raise ValueError("Zuordnungsbetrag muss bei Rechnung positiv sein")
            if expected_sign < 0 and item.amount_cents > 0:
                raise ValueError("Zuordnungsbetrag muss bei Gutschrift negativ sein")
            if subcategory_type_ids:
                parent_cost_type_id = subcategory_type_ids.get(item.cost_subcategory_id)
                if parent_cost_type_id is not None and parent_cost_type_id != item.cost_type_id:
                    raise ValueError("Unterkategorie passt nicht zur Kostenkategorie")
            total += item.amount_cents

        if total != gross_cents:
            raise ValueError("Kostenzuordnungen müssen die Belegsumme vollständig abdecken")

    def _default_cost_area_id(self, session: Session) -> int | None:
        default_cost_area = session.exec(select(CostArea).where(CostArea.name == DEFAULT_HIDDEN_COST_AREA_NAME)).first()
        if not default_cost_area or default_cost_area.id is None:
            return None
        return default_cost_area.id

    def _to_optional_positive_int(self, value: int | None) -> int | None:
        if value is None:
            return None
        return int(value) if int(value) > 0 else None

    def _is_empty_allocation(self, allocation: AllocationInput) -> bool:
        return (
            allocation.cost_type_id is None
            and allocation.cost_subcategory_id is None
            and allocation.project_id is None
            and allocation.cost_area_id is None
            and (allocation.amount_cents is None or allocation.amount_cents == 0)
        )
