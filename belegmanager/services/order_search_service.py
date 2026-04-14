from __future__ import annotations

from datetime import date

from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from ..db import engine
from ..models import Order, OrderItem
from .order_service import order_status_key


class OrderSearchService:
    def __init__(self, db_engine=engine) -> None:
        self._engine = db_engine

    def search(
        self,
        *,
        query: str = "",
        contact_ids: list[int] | None = None,
        project_ids: list[int] | None = None,
        statuses: list[str] | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        include_deleted: bool = False,
        deleted_only: bool = False,
    ) -> list[Order]:
        contact_ids = contact_ids or []
        project_ids = project_ids or []
        statuses = [item for item in (statuses or []) if item in {"draft", "invoiced"}]
        query_value = (query or "").strip().casefold()

        with Session(self._engine) as session:
            stmt = (
                select(Order)
                .options(
                    selectinload(Order.contact),
                    selectinload(Order.items).selectinload(OrderItem.project),
                )
                .order_by(Order.sale_date.desc(), Order.created_at.desc())
            )
            if deleted_only:
                stmt = stmt.where(Order.deleted_at.is_not(None))
            elif not include_deleted:
                stmt = stmt.where(Order.deleted_at.is_(None))
            results = list(session.exec(stmt).all())

        filtered: list[Order] = []
        for order in results:
            if contact_ids and order.contact_id not in contact_ids:
                continue
            if project_ids:
                order_project_ids = {item.project_id for item in order.items if item.project_id is not None}
                if not order_project_ids.intersection(project_ids):
                    continue
            if statuses and order_status_key(order) not in statuses:
                continue
            if date_from and order.sale_date < date_from:
                continue
            if date_to and order.sale_date > date_to:
                continue
            if query_value and not self._matches_query(order, query_value):
                continue
            filtered.append(order)
        return filtered

    def _matches_query(self, order: Order, query_value: str) -> bool:
        candidates = [
            order.internal_number,
            order.invoice_number or "",
            order.notes or "",
        ]
        if order.contact is not None:
            candidates.extend(
                [
                    order.contact.given_name or "",
                    order.contact.family_name or "",
                    order.contact.organisation or "",
                    order.contact.email or "",
                ]
            )
        return any(query_value in candidate.casefold() for candidate in candidates if candidate)
