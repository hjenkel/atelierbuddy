from __future__ import annotations

from datetime import date

from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from ..db import engine
from ..fts import search_fts_receipt_ids
from ..models import CostAllocation, Receipt


class SearchService:
    def search(
        self,
        query: str = "",
        project_ids: list[int] | None = None,
        cost_type_ids: list[int] | None = None,
        cost_subcategory_ids: list[int] | None = None,
        cost_area_ids: list[int] | None = None,
        supplier_ids: list[int] | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        *,
        include_deleted: bool = False,
        deleted_only: bool = False,
    ) -> list[Receipt]:
        project_ids = project_ids or []
        cost_type_ids = cost_type_ids or []
        cost_subcategory_ids = cost_subcategory_ids or []
        cost_area_ids = cost_area_ids or []
        supplier_ids = supplier_ids or []

        with Session(engine) as session:
            stmt = (
                select(Receipt)
                .options(
                    selectinload(Receipt.supplier),
                    selectinload(Receipt.allocations).selectinload(CostAllocation.cost_type),
                    selectinload(Receipt.allocations).selectinload(CostAllocation.cost_subcategory),
                    selectinload(Receipt.allocations).selectinload(CostAllocation.project),
                    selectinload(Receipt.allocations).selectinload(CostAllocation.cost_area),
                )
                .order_by(Receipt.created_at.desc())
            )

            if deleted_only:
                stmt = stmt.where(Receipt.deleted_at.is_not(None))
            elif not include_deleted:
                stmt = stmt.where(Receipt.deleted_at.is_(None))

            if query.strip():
                ids = search_fts_receipt_ids(session, query)
                if not ids:
                    return []
                stmt = stmt.where(Receipt.id.in_(ids))

            if supplier_ids:
                stmt = stmt.where(Receipt.supplier_id.in_(supplier_ids))

            if date_from:
                stmt = stmt.where(Receipt.doc_date >= date_from)

            if date_to:
                stmt = stmt.where(Receipt.doc_date <= date_to)

            if project_ids:
                project_match = select(CostAllocation.receipt_id).where(CostAllocation.project_id.in_(project_ids))
                stmt = stmt.where(Receipt.id.in_(project_match))

            if cost_type_ids:
                cost_type_match = select(CostAllocation.receipt_id).where(CostAllocation.cost_type_id.in_(cost_type_ids))
                stmt = stmt.where(Receipt.id.in_(cost_type_match))

            if cost_subcategory_ids:
                subcategory_match = select(CostAllocation.receipt_id).where(
                    CostAllocation.cost_subcategory_id.in_(cost_subcategory_ids)
                )
                stmt = stmt.where(Receipt.id.in_(subcategory_match))

            if cost_area_ids:
                cost_area_match = select(CostAllocation.receipt_id).where(CostAllocation.cost_area_id.in_(cost_area_ids))
                stmt = stmt.where(Receipt.id.in_(cost_area_match))

            results = list(session.exec(stmt).all())

            deduped: list[Receipt] = []
            seen: set[int] = set()
            for receipt in results:
                if receipt.id is None or receipt.id in seen:
                    continue
                deduped.append(receipt)
                seen.add(receipt.id)
            return deduped
