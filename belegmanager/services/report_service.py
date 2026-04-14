from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from ..db import engine
from ..models import CostAllocation, CostSubcategory, CostType, Order, OrderItem, Receipt
from ..schemas import (
    CategoryTotalRow,
    IncomeOrderRow,
    IncomeProjectTotalRow,
    IncomeReportTotals,
    ReportTotals,
    SubcategoryTotalRow,
)
from .order_service import order_item_total_cents


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

    def build_income_summary(self, date_from: date | None, date_to: date | None) -> IncomeReportTotals:
        orders = self._invoiced_orders(date_from=date_from, date_to=date_to)
        totals_by_project_cents: dict[tuple[int, str], int] = defaultdict(int)
        overall_total_cents = 0

        for order in orders:
            overall_total_cents += self._order_total_cents(order)
            for item in order.items:
                if item.project is None or item.project.id is None:
                    key = (0, "Ohne Projekt")
                else:
                    key = (item.project.id, item.project.name)
                totals_by_project_cents[key] += order_item_total_cents(item.quantity, item.unit_price_cents)

        totals_by_project = [
            IncomeProjectTotalRow(project_id=project_id, project_name=project_name, total_cents=total_cents)
            for (project_id, project_name), total_cents in totals_by_project_cents.items()
        ]
        totals_by_project.sort(key=lambda row: (-abs(row.total_cents), row.project_name.casefold()))

        return IncomeReportTotals(
            order_count=len(orders),
            overall_total_cents=overall_total_cents,
            totals_by_project=totals_by_project,
        )

    def build_income_order_breakdown(
        self,
        date_from: date | None,
        date_to: date | None,
        project_id: int,
    ) -> list[IncomeOrderRow]:
        if project_id < 0:
            return []

        rows: list[IncomeOrderRow] = []
        for order in self._invoiced_orders(date_from=date_from, date_to=date_to):
            project_total_cents = 0
            for item in order.items:
                if project_id == 0:
                    if item.project_id is not None:
                        continue
                elif item.project_id != project_id:
                    continue
                project_total_cents += order_item_total_cents(item.quantity, item.unit_price_cents)
            if project_total_cents == 0:
                continue
            rows.append(
                IncomeOrderRow(
                    order_id=order.id or 0,
                    internal_number=order.internal_number,
                    contact_name=self._contact_name(order),
                    invoice_date=order.invoice_date,
                    total_cents=project_total_cents,
                )
            )
        rows.sort(key=lambda row: (row.invoice_date, row.internal_number), reverse=True)
        return rows

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

    def _invoiced_orders(self, date_from: date | None, date_to: date | None) -> list[Order]:
        with Session(self._engine) as session:
            stmt = (
                select(Order)
                .where(
                    Order.deleted_at.is_(None),
                    Order.invoice_date.is_not(None),
                )
                .options(
                    selectinload(Order.contact),
                    selectinload(Order.items).selectinload(OrderItem.project),
                )
                .order_by(Order.invoice_date.desc(), Order.created_at.desc())
            )
            if date_from:
                stmt = stmt.where(Order.invoice_date >= date_from)
            if date_to:
                stmt = stmt.where(Order.invoice_date <= date_to)
            orders = list(session.exec(stmt).all())

        return [order for order in orders if order.items]

    def _order_total_cents(self, order: Order) -> int:
        return sum(order_item_total_cents(item.quantity, item.unit_price_cents) for item in order.items)

    def _contact_name(self, order: Order) -> str:
        if order.contact is None:
            return "-"
        parts = [part.strip() for part in (order.contact.given_name, order.contact.family_name) if (part or "").strip()]
        if parts:
            return " ".join(parts)
        organisation = (order.contact.organisation or "").strip()
        return organisation or "-"
