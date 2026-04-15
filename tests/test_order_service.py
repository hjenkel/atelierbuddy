from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlmodel import SQLModel, Session, create_engine, select

from belegmanager.models import Contact, ContactCategory, Order, Project
from belegmanager.schemas import OrderItemInput
from belegmanager.services.order_search_service import OrderSearchService
from belegmanager.services.order_service import OrderService, order_item_total_cents, order_status_key, order_status_label


def _build_services() -> tuple[OrderService, OrderSearchService, object]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return OrderService(db_engine=engine), OrderSearchService(db_engine=engine), engine


def _seed_contact_and_project(engine: object) -> tuple[int, int]:
    with Session(engine) as session:
        category = ContactCategory(name="Interessent / Kunde", icon="handshake")
        contact = Contact(given_name="Alex", family_name="Meyer", contact_category=category)
        project = Project(name="Poster", color="#2ec4b6", active=True)
        session.add(category)
        session.add(contact)
        session.add(project)
        session.commit()
        session.refresh(contact)
        session.refresh(project)
        return contact.id or 0, project.id or 0


def test_create_order_assigns_yearly_internal_numbers_and_keeps_number_on_save() -> None:
    service, _, engine = _build_services()
    contact_id, project_id = _seed_contact_and_project(engine)

    first = service.create_order(contact_id=contact_id, sale_date=date(2026, 1, 10))
    second = service.create_order(contact_id=contact_id, sale_date=date(2026, 6, 1))
    third = service.create_order(contact_id=contact_id, sale_date=date(2027, 1, 1))

    assert first.internal_number == "2026-0001"
    assert second.internal_number == "2026-0002"
    assert third.internal_number == "2027-0001"

    saved = service.save_order(
        order_id=first.id or 0,
        contact_id=contact_id,
        sale_date=date(2028, 2, 2),
        invoice_date=None,
        invoice_number=None,
        notes="Nachträglich bearbeitet",
        items=[
            OrderItemInput(
                description="Posterverkauf",
                quantity=Decimal("1.000"),
                unit_price_cents=25000,
                project_id=project_id,
                position=1,
            )
        ],
    )

    assert saved.internal_number == "2026-0001"


def test_save_order_requires_invoice_number_once_invoice_date_is_set() -> None:
    service, _, engine = _build_services()
    contact_id, project_id = _seed_contact_and_project(engine)
    order = service.create_order(contact_id=contact_id, sale_date=date(2026, 1, 10))

    try:
        service.save_order(
            order_id=order.id or 0,
            contact_id=contact_id,
            sale_date=date(2026, 1, 10),
            invoice_date=date(2026, 1, 12),
            invoice_number=" ",
            notes=None,
            items=[
                OrderItemInput(
                    description="Posterverkauf",
                    quantity=Decimal("1.000"),
                    unit_price_cents=12000,
                    project_id=project_id,
                    position=1,
                )
            ],
        )
    except ValueError as exc:
        assert "Rechnungsnummer fehlt" in str(exc)
    else:
        raise AssertionError("expected ValueError for missing invoice number")


def test_save_order_requires_at_least_one_item() -> None:
    service, _, engine = _build_services()
    contact_id, _ = _seed_contact_and_project(engine)
    order = service.create_order(contact_id=contact_id, sale_date=date(2026, 1, 10))

    try:
        service.save_order(
            order_id=order.id or 0,
            contact_id=contact_id,
            sale_date=date(2026, 1, 10),
            invoice_date=None,
            invoice_number=None,
            notes=None,
            items=[],
        )
    except ValueError as exc:
        assert "Mindestens eine Position" in str(exc)
    else:
        raise AssertionError("expected ValueError for empty item list")


def test_save_order_allows_items_without_project() -> None:
    service, _, engine = _build_services()
    contact_id, _ = _seed_contact_and_project(engine)
    order = service.create_order(contact_id=contact_id, sale_date=date(2026, 1, 10))

    saved = service.save_order(
        order_id=order.id or 0,
        contact_id=contact_id,
        sale_date=date(2026, 1, 10),
        invoice_date=None,
        invoice_number=None,
        notes=None,
        items=[
            OrderItemInput(
                description="Freie Leistung",
                quantity=Decimal("1.000"),
                unit_price_cents=12000,
                project_id=None,
                position=1,
            )
        ],
    )

    assert len(saved.items) == 1
    assert saved.items[0].project_id is None


def test_invoice_number_must_be_unique_after_trimming() -> None:
    service, _, engine = _build_services()
    contact_id, project_id = _seed_contact_and_project(engine)
    first = service.create_order(contact_id=contact_id, sale_date=date(2026, 1, 10))
    second = service.create_order(contact_id=contact_id, sale_date=date(2026, 1, 11))

    service.save_order(
        order_id=first.id or 0,
        contact_id=contact_id,
        sale_date=date(2026, 1, 10),
        invoice_date=date(2026, 1, 12),
        invoice_number=" RE-2026-01 ",
        notes=None,
        items=[
            OrderItemInput(
                description="Posterverkauf",
                quantity=Decimal("1.000"),
                unit_price_cents=12000,
                project_id=project_id,
                position=1,
            )
        ],
    )

    try:
        service.save_order(
            order_id=second.id or 0,
            contact_id=contact_id,
            sale_date=date(2026, 1, 11),
            invoice_date=date(2026, 1, 13),
            invoice_number="RE-2026-01",
            notes=None,
            items=[
                OrderItemInput(
                    description="Weiterer Verkauf",
                    quantity=Decimal("1.000"),
                    unit_price_cents=9000,
                    project_id=project_id,
                    position=1,
                )
            ],
        )
    except ValueError as exc:
        assert "existiert bereits" in str(exc)
    else:
        raise AssertionError("expected ValueError for duplicate invoice number")


def test_negative_item_values_and_rounding_are_supported() -> None:
    assert order_item_total_cents(Decimal("1.005"), 100) == 101
    assert order_item_total_cents(Decimal("1.005"), -100) == -101


def test_order_search_filters_by_status_and_project() -> None:
    service, search_service, engine = _build_services()
    contact_id, project_id = _seed_contact_and_project(engine)
    with Session(engine) as session:
        second_project = Project(name="Print", color="#123456", active=True)
        session.add(second_project)
        session.commit()
        session.refresh(second_project)
        second_project_id = second_project.id or 0

    draft_order = service.create_order(contact_id=contact_id, sale_date=date(2026, 1, 10))
    invoiced_order = service.create_order(contact_id=contact_id, sale_date=date(2026, 1, 11))

    service.save_order(
        order_id=draft_order.id or 0,
        contact_id=contact_id,
        sale_date=date(2026, 1, 10),
        invoice_date=None,
        invoice_number=None,
        notes="Entwurf",
        items=[
            OrderItemInput(
                description="Posterverkauf",
                quantity=Decimal("1.000"),
                unit_price_cents=12000,
                project_id=project_id,
                position=1,
            )
        ],
    )
    service.save_order(
        order_id=invoiced_order.id or 0,
        contact_id=contact_id,
        sale_date=date(2026, 1, 11),
        invoice_date=date(2026, 1, 12),
        invoice_number="RE-2026-02",
        notes="Abgerechnet",
        items=[
            OrderItemInput(
                description="Printverkauf",
                quantity=Decimal("1.000"),
                unit_price_cents=18000,
                project_id=second_project_id,
                position=1,
            )
        ],
    )
    service.set_invoice_document(
        order_id=invoiced_order.id or 0,
        document_path="/tmp/re-2026-02.pdf",
        original_filename="re-2026-02.pdf",
    )

    invoiced_results = search_service.search(statuses=["invoiced"])
    project_results = search_service.search(project_ids=[second_project_id])

    assert [item.id for item in invoiced_results] == [invoiced_order.id]
    assert [item.id for item in project_results] == [invoiced_order.id]

    with Session(engine) as session:
        stored_order = session.exec(select(Order).where(Order.id == invoiced_order.id)).first()
    assert stored_order is not None
    assert stored_order.invoice_number == "RE-2026-02"


def test_order_status_distinguishes_draft_missing_document_and_invoiced() -> None:
    service, search_service, engine = _build_services()
    contact_id, project_id = _seed_contact_and_project(engine)
    draft_order = service.create_order(contact_id=contact_id, sale_date=date(2026, 1, 10))
    document_missing_order = service.create_order(contact_id=contact_id, sale_date=date(2026, 1, 11))
    invoiced_order = service.create_order(contact_id=contact_id, sale_date=date(2026, 1, 12))

    service.save_order(
        order_id=draft_order.id or 0,
        contact_id=contact_id,
        sale_date=date(2026, 1, 10),
        invoice_date=None,
        invoice_number=None,
        notes=None,
        items=[
            OrderItemInput(
                description="Entwurf",
                quantity=Decimal("1.000"),
                unit_price_cents=12000,
                project_id=project_id,
                position=1,
            )
        ],
    )
    service.save_order(
        order_id=document_missing_order.id or 0,
        contact_id=contact_id,
        sale_date=date(2026, 1, 11),
        invoice_date=None,
        invoice_number="RE-2026-11",
        notes=None,
        items=[
            OrderItemInput(
                description="Dokument fehlt",
                quantity=Decimal("1.000"),
                unit_price_cents=12000,
                project_id=project_id,
                position=1,
            )
        ],
    )
    service.save_order(
        order_id=invoiced_order.id or 0,
        contact_id=contact_id,
        sale_date=date(2026, 1, 12),
        invoice_date=date(2026, 1, 13),
        invoice_number="RE-2026-12",
        notes=None,
        items=[
            OrderItemInput(
                description="Abgerechnet",
                quantity=Decimal("1.000"),
                unit_price_cents=12000,
                project_id=project_id,
                position=1,
            )
        ],
    )
    service.set_invoice_document(
        order_id=invoiced_order.id or 0,
        document_path="/tmp/re-2026-12.pdf",
        original_filename="re-2026-12.pdf",
    )

    with Session(engine) as session:
        stored_draft = session.get(Order, draft_order.id)
        stored_missing = session.get(Order, document_missing_order.id)
        stored_invoiced = session.get(Order, invoiced_order.id)

    assert stored_draft is not None
    assert stored_missing is not None
    assert stored_invoiced is not None
    assert order_status_key(stored_draft) == "draft"
    assert order_status_label(stored_draft) == "Entwurf"
    assert order_status_key(stored_missing) == "document_missing"
    assert order_status_label(stored_missing) == "Dokument fehlt"
    assert order_status_key(stored_invoiced) == "invoiced"
    assert order_status_label(stored_invoiced) == "Abgerechnet"

    missing_results = search_service.search(statuses=["document_missing"])
    assert [item.id for item in missing_results] == [document_missing_order.id]


def test_invoiced_order_cannot_be_moved_to_trash_or_hard_deleted() -> None:
    service, _, engine = _build_services()
    contact_id, project_id = _seed_contact_and_project(engine)
    order = service.create_order(contact_id=contact_id, sale_date=date(2026, 1, 10))

    service.save_order(
        order_id=order.id or 0,
        contact_id=contact_id,
        sale_date=date(2026, 1, 10),
        invoice_date=date(2026, 1, 12),
        invoice_number="RE-2026-09",
        notes=None,
        items=[
            OrderItemInput(
                description="Rechnung",
                quantity=Decimal("1.000"),
                unit_price_cents=12000,
                project_id=project_id,
                position=1,
            )
        ],
    )

    try:
        service.move_to_trash(order.id or 0)
    except ValueError as exc:
        assert "nicht gelöscht oder archiviert" in str(exc)
    else:
        raise AssertionError("expected ValueError for deleting invoiced order")

    try:
        service.hard_delete(order.id or 0)
    except ValueError as exc:
        assert "nicht gelöscht oder archiviert" in str(exc)
    else:
        raise AssertionError("expected ValueError for hard deleting invoiced order")


def test_order_with_invoice_number_only_cannot_be_moved_to_trash_or_hard_deleted() -> None:
    service, _, engine = _build_services()
    contact_id, project_id = _seed_contact_and_project(engine)
    order = service.create_order(contact_id=contact_id, sale_date=date(2026, 1, 10))

    service.save_order(
        order_id=order.id or 0,
        contact_id=contact_id,
        sale_date=date(2026, 1, 10),
        invoice_date=None,
        invoice_number="RE-2026-10",
        notes=None,
        items=[
            OrderItemInput(
                description="Rechnung",
                quantity=Decimal("1.000"),
                unit_price_cents=12000,
                project_id=project_id,
                position=1,
            )
        ],
    )

    try:
        service.move_to_trash(order.id or 0)
    except ValueError as exc:
        assert "nicht gelöscht oder archiviert" in str(exc)
    else:
        raise AssertionError("expected ValueError for archiving numbered order")

    try:
        service.hard_delete(order.id or 0)
    except ValueError as exc:
        assert "nicht gelöscht oder archiviert" in str(exc)
    else:
        raise AssertionError("expected ValueError for hard deleting numbered order")


def test_order_with_invoice_document_only_cannot_be_moved_to_trash_or_hard_deleted() -> None:
    service, _, engine = _build_services()
    contact_id, _ = _seed_contact_and_project(engine)
    order = service.create_order(contact_id=contact_id, sale_date=date(2026, 1, 10))

    old_path = service.set_invoice_document(
        order_id=order.id or 0,
        document_path="/tmp/rechnung.pdf",
        original_filename="rechnung.pdf",
    )
    assert old_path is None

    try:
        service.move_to_trash(order.id or 0)
    except ValueError as exc:
        assert "nicht gelöscht oder archiviert" in str(exc)
    else:
        raise AssertionError("expected ValueError for archiving order with document")

    try:
        service.hard_delete(order.id or 0)
    except ValueError as exc:
        assert "nicht gelöscht oder archiviert" in str(exc)
    else:
        raise AssertionError("expected ValueError for hard deleting order with document")
