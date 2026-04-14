from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable

from sqlalchemy import delete, func
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from ..db import engine
from ..models import Contact, Order, OrderItem, Project
from ..schemas import OrderItemInput

QUANTITY_STEP = Decimal("0.001")
MAX_DESCRIPTION_LENGTH = 255
MAX_NOTES_LENGTH = 5000


def order_status_key(order: Order) -> str:
    if order.invoice_date is not None:
        return "invoiced"
    return "draft"


def order_status_label(order: Order) -> str:
    return {
        "draft": "Entwurf",
        "invoiced": "Abgerechnet",
    }[order_status_key(order)]


def order_item_total_cents(quantity: Decimal, unit_price_cents: int) -> int:
    total = (quantity * Decimal(unit_price_cents)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(total)


def order_total_cents(items: Iterable[OrderItem | OrderItemInput]) -> int:
    total = 0
    for item in items:
        total += order_item_total_cents(Decimal(item.quantity), int(item.unit_price_cents))
    return total


class OrderService:
    def __init__(self, db_engine=engine) -> None:
        self._engine = db_engine

    def create_order(self, *, contact_id: int, sale_date: date | None = None) -> Order:
        effective_sale_date = sale_date or date.today()
        with Session(self._engine) as session:
            contact = session.get(Contact, contact_id)
            if contact is None:
                raise ValueError("Kontakt nicht gefunden")
            order = Order(
                contact_id=contact_id,
                sale_date=effective_sale_date,
                internal_number=self._next_internal_number(session, effective_sale_date),
            )
            session.add(order)
            session.commit()
            session.refresh(order)
            return order

    def save_order(
        self,
        *,
        order_id: int,
        contact_id: int,
        sale_date: date | None,
        invoice_date: date | None,
        invoice_number: str | None,
        notes: str | None,
        items: list[OrderItemInput],
    ) -> Order:
        if sale_date is None:
            raise ValueError("Verkaufsdatum fehlt")
        normalized_invoice_number = self._normalize_invoice_number(invoice_number)
        normalized_notes = self._normalize_notes(notes)
        if invoice_date is not None and normalized_invoice_number is None:
            raise ValueError("Rechnungsnummer fehlt")
        normalized_items = self._normalize_items(items)

        with Session(self._engine) as session:
            order = session.exec(
                select(Order)
                .where(Order.id == order_id)
                .options(selectinload(Order.items))
            ).first()
            if order is None:
                raise ValueError("Verkauf nicht gefunden")
            if order.deleted_at is not None:
                raise ValueError("Gelöschter Verkauf kann nicht gespeichert werden")

            contact = session.get(Contact, contact_id)
            if contact is None:
                raise ValueError("Kontakt nicht gefunden")

            self._ensure_invoice_number_available(session, normalized_invoice_number, order_id)
            self._ensure_projects_exist(session, normalized_items)

            order.contact_id = contact_id
            order.sale_date = sale_date
            order.invoice_date = invoice_date
            order.invoice_number = normalized_invoice_number
            order.notes = normalized_notes
            order.updated_at = datetime.now(timezone.utc)
            session.add(order)

            session.exec(delete(OrderItem).where(OrderItem.order_id == order_id))
            session.flush()

            for item in normalized_items:
                session.add(
                    OrderItem(
                        order_id=order_id,
                        position=item.position,
                        description=item.description,
                        quantity=item.quantity,
                        unit_price_cents=item.unit_price_cents,
                        project_id=item.project_id,
                    )
                )

            session.commit()
            refreshed = session.exec(
                select(Order)
                .where(Order.id == order_id)
                .options(
                    selectinload(Order.contact),
                    selectinload(Order.items).selectinload(OrderItem.project),
                )
            ).first()
            if refreshed is None:
                raise ValueError("Verkauf nicht gefunden")
            return refreshed

    def move_to_trash(self, order_id: int) -> None:
        with Session(self._engine) as session:
            order = session.get(Order, order_id)
            if order is None:
                raise ValueError("Verkauf nicht gefunden")
            if order.deleted_at is not None:
                return
            self._ensure_order_can_be_deleted(order)
            order.deleted_at = datetime.now(timezone.utc)
            order.updated_at = datetime.now(timezone.utc)
            session.add(order)
            session.commit()

    def restore_from_trash(self, order_id: int) -> None:
        with Session(self._engine) as session:
            order = session.get(Order, order_id)
            if order is None:
                raise ValueError("Verkauf nicht gefunden")
            order.deleted_at = None
            order.updated_at = datetime.now(timezone.utc)
            session.add(order)
            session.commit()

    def hard_delete(self, order_id: int) -> None:
        with Session(self._engine) as session:
            order = session.get(Order, order_id)
            if order is None:
                raise ValueError("Verkauf nicht gefunden")
            self._ensure_order_can_be_deleted(order)
            session.exec(delete(OrderItem).where(OrderItem.order_id == order_id))
            session.delete(order)
            session.commit()

    def _next_internal_number(self, session: Session, sale_date_value: date) -> str:
        year_prefix = f"{sale_date_value.year}-"
        existing_numbers = session.exec(
            select(Order.internal_number).where(Order.internal_number.startswith(year_prefix))
        ).all()
        max_sequence = 0
        for value in existing_numbers:
            if not value or "-" not in value:
                continue
            _, sequence_part = value.split("-", 1)
            if sequence_part.isdigit():
                max_sequence = max(max_sequence, int(sequence_part))
        return f"{sale_date_value.year}-{max_sequence + 1:04d}"

    def _normalize_invoice_number(self, invoice_number: str | None) -> str | None:
        value = (invoice_number or "").strip()
        return value or None

    def _normalize_notes(self, notes: str | None) -> str | None:
        value = (notes or "").strip()
        if not value:
            return None
        if len(value) > MAX_NOTES_LENGTH:
            raise ValueError(f"Notiz darf maximal {MAX_NOTES_LENGTH} Zeichen lang sein")
        return value

    def _normalize_items(self, items: list[OrderItemInput]) -> list[OrderItemInput]:
        if not items:
            raise ValueError("Mindestens eine Position ist erforderlich")

        normalized_items: list[OrderItemInput] = []
        for index, item in enumerate(items, start=1):
            description = (item.description or "").strip()
            if not description:
                raise ValueError(f"Bezeichnung fehlt in Position {index}")
            if len(description) > MAX_DESCRIPTION_LENGTH:
                raise ValueError(
                    f"Bezeichnung in Position {index} darf maximal {MAX_DESCRIPTION_LENGTH} Zeichen lang sein"
                )

            quantity = self._normalize_quantity(item.quantity, position=index)
            try:
                unit_price_cents = int(item.unit_price_cents)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Einzelpreis in Position {index} ist ungültig") from exc

            project_id: int | None = None
            if item.project_id is not None:
                try:
                    project_id = int(item.project_id)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"Projekt in Position {index} ist ungültig") from exc
                if project_id <= 0:
                    raise ValueError(f"Projekt in Position {index} ist ungültig")

            normalized_items.append(
                OrderItemInput(
                    description=description,
                    quantity=quantity,
                    unit_price_cents=unit_price_cents,
                    project_id=project_id,
                    position=index,
                )
            )

        return normalized_items

    def _normalize_quantity(self, value: Decimal, *, position: int) -> Decimal:
        try:
            quantity = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"Menge in Position {position} ist ungültig") from exc
        if quantity <= 0:
            raise ValueError(f"Menge in Position {position} muss größer als 0 sein")
        if quantity.as_tuple().exponent < -3:
            raise ValueError(f"Menge in Position {position} darf maximal 3 Nachkommastellen haben")
        return quantity.quantize(QUANTITY_STEP)

    def _ensure_invoice_number_available(
        self,
        session: Session,
        invoice_number: str | None,
        order_id: int,
    ) -> None:
        if invoice_number is None:
            return
        existing = session.exec(
            select(Order).where(
                Order.invoice_number == invoice_number,
                Order.id != order_id,
            )
        ).first()
        if existing is not None:
            raise ValueError("Rechnungsnummer existiert bereits")

    def _ensure_projects_exist(self, session: Session, items: list[OrderItemInput]) -> None:
        for item in items:
            if item.project_id is None:
                continue
            project = session.get(Project, item.project_id)
            if project is None:
                raise ValueError(f"Projekt in Position {item.position} nicht gefunden")

    def _ensure_order_can_be_deleted(self, order: Order) -> None:
        if order.invoice_date is not None or (order.invoice_number or "").strip():
            raise ValueError("Abgerechnete Verkäufe können nicht gelöscht oder archiviert werden")
