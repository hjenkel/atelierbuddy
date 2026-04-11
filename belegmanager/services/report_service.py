from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import func
from sqlmodel import Session, select

from ..db import engine
from ..models import CostAllocation, CostSubcategory, CostType, Receipt
from ..schemas import CategoryTotalRow, ReportTotals, SubcategoryTotalRow


class ReportService:
    def __init__(self, db_engine: Any = None) -> None:
        self._engine = db_engine or engine

    def build_summary(self, date_from: date | None, date_to: date | None) -> ReportTotals:
        with Session(self._engine) as session:
            valid_receipt_ids_stmt = self._valid_receipt_ids_stmt(date_from=date_from, date_to=date_to)

            receipt_count_raw = session.exec(select(func.count()).select_from(valid_receipt_ids_stmt)).first()
            receipt_count = int(receipt_count_raw or 0)

            overall_total_raw = session.exec(
                select(func.coalesce(func.sum(Receipt.amount_gross_cents), 0)).where(
                    Receipt.id.in_(select(valid_receipt_ids_stmt.c.receipt_id))
                )
            ).first()
            overall_total_cents = int(overall_total_raw or 0)

            category_rows_raw = session.exec(
                select(
                    CostAllocation.cost_type_id,
                    CostType.name,
                    func.coalesce(func.sum(CostAllocation.amount_cents), 0).label("total_cents"),
                )
                .join(CostType, CostType.id == CostAllocation.cost_type_id)
                .where(CostAllocation.receipt_id.in_(select(valid_receipt_ids_stmt.c.receipt_id)))
                .group_by(CostAllocation.cost_type_id, CostType.name)
                .order_by(func.abs(func.coalesce(func.sum(CostAllocation.amount_cents), 0)).desc(), CostType.name.asc())
            ).all()

            totals_by_cost_type = [
                CategoryTotalRow(
                    cost_type_id=int(cost_type_id),
                    cost_type_name=str(cost_type_name or ""),
                    total_cents=int(total_cents or 0),
                )
                for cost_type_id, cost_type_name, total_cents in category_rows_raw
            ]

        return ReportTotals(
            receipt_count=receipt_count,
            overall_total_cents=overall_total_cents,
            totals_by_cost_type=totals_by_cost_type,
        )

    def build_subcategory_breakdown(
        self,
        date_from: date | None,
        date_to: date | None,
        cost_type_id: int,
    ) -> list[SubcategoryTotalRow]:
        if cost_type_id <= 0:
            return []

        with Session(self._engine) as session:
            valid_receipt_ids_stmt = self._valid_receipt_ids_stmt(date_from=date_from, date_to=date_to)

            rows_raw = session.exec(
                select(
                    CostAllocation.cost_subcategory_id,
                    CostSubcategory.name,
                    func.coalesce(func.sum(CostAllocation.amount_cents), 0).label("total_cents"),
                )
                .join(CostSubcategory, CostSubcategory.id == CostAllocation.cost_subcategory_id)
                .where(
                    CostAllocation.receipt_id.in_(select(valid_receipt_ids_stmt.c.receipt_id)),
                    CostAllocation.cost_type_id == cost_type_id,
                )
                .group_by(CostAllocation.cost_subcategory_id, CostSubcategory.name)
                .order_by(
                    func.abs(func.coalesce(func.sum(CostAllocation.amount_cents), 0)).desc(),
                    CostSubcategory.name.asc(),
                )
            ).all()

        return [
            SubcategoryTotalRow(
                cost_subcategory_id=int(subcategory_id),
                cost_subcategory_name=str(subcategory_name or ""),
                total_cents=int(total_cents or 0),
            )
            for subcategory_id, subcategory_name, total_cents in rows_raw
            if subcategory_id is not None
        ]

    def _valid_receipt_ids_stmt(self, date_from: date | None, date_to: date | None):
        stmt = (
            select(Receipt.id.label("receipt_id"))
            .join(CostAllocation, CostAllocation.receipt_id == Receipt.id)
            .where(
                Receipt.deleted_at.is_(None),
                Receipt.doc_date.is_not(None),
                Receipt.amount_gross_cents.is_not(None),
            )
            .group_by(Receipt.id, Receipt.amount_gross_cents)
            .having(func.count(CostAllocation.id) > 0)
            .having(func.coalesce(func.sum(CostAllocation.amount_cents), 0) == Receipt.amount_gross_cents)
        )
        if date_from:
            stmt = stmt.where(Receipt.doc_date >= date_from)
        if date_to:
            stmt = stmt.where(Receipt.doc_date <= date_to)
        return stmt.subquery("valid_receipts")
