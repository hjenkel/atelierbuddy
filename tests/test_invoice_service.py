from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlmodel import SQLModel, Session, create_engine, select

from belegmanager.config import Settings
from belegmanager.models import Contact, ContactCategory, Order, OrderItem
from belegmanager.schemas import OrderItemInput
from belegmanager.services import invoice_service as invoice_service_module
from belegmanager.services.invoice_service import InvoiceService
from belegmanager.services.order_service import OrderService
from belegmanager.utils import storage


class StubRenderer:
    def __init__(self) -> None:
        self.last_html = ""
        self.last_stylesheet_path: Path | None = None
        self.last_base_url = ""

    def render(self, html: str, *, stylesheet_path: Path, base_url: str, destination: Path) -> None:
        self.last_html = html
        self.last_stylesheet_path = stylesheet_path
        self.last_base_url = base_url
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"%PDF-1.7\n%stub\n")


class FailingRenderer(StubRenderer):
    def render(self, html: str, *, stylesheet_path: Path, base_url: str, destination: Path) -> None:
        super().render(html, stylesheet_path=stylesheet_path, base_url=base_url, destination=destination)
        raise RuntimeError("kaputt")


def _temp_settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    archive_dir = data_dir / "archive"
    return Settings(
        root_dir=tmp_path,
        data_dir=data_dir,
        assets_dir=tmp_path / "assets",
        db_path=data_dir / "belegmanager.db",
        archive_dir=archive_dir,
        originals_dir=archive_dir / "originals",
        normalized_dir=archive_dir / "normalized",
        ocr_dir=archive_dir / "ocr",
        thumbs_dir=archive_dir / "thumbs",
        order_invoices_dir=archive_dir / "order_invoices",
        invoice_assets_dir=archive_dir / "invoice_assets",
        invoice_logos_dir=archive_dir / "invoice_assets" / "logos",
        custom_invoice_template_dir=data_dir / "invoice_templates" / "custom",
        custom_invoice_fonts_dir=data_dir / "invoice_templates" / "custom" / "fonts",
        works_cover_dir=archive_dir / "work_covers",
    )


def _template_dir(tmp_path: Path) -> Path:
    template_dir = tmp_path / "templates"
    template_dir.mkdir(parents=True, exist_ok=True)
    (template_dir / "invoice.html").write_text(
        "<html><body><h1>$invoice_number</h1><div>$recipient_html</div><div>$items_html</div><div>$total_net</div>"
        "<div>$bank_account_holder</div><div>$iban</div><div>$bic</div><div>§19 UStG</div><div>$logo_html</div></body></html>",
        encoding="utf-8",
    )
    (template_dir / "invoice.css").write_text("body { font-family: sans-serif; }", encoding="utf-8")
    return template_dir


def _build_services(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[OrderService, InvoiceService, object, StubRenderer]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    temp_settings = _temp_settings(tmp_path)
    temp_settings.ensure_dirs()
    monkeypatch.setattr(storage, "settings", temp_settings)
    monkeypatch.setattr(invoice_service_module, "settings", temp_settings)
    renderer = StubRenderer()
    order_service = OrderService(db_engine=engine)
    invoice_service = InvoiceService(
        db_engine=engine,
        renderer=renderer,
        template_dir=_template_dir(tmp_path),
        today_provider=lambda: date(2026, 4, 18),
    )
    return order_service, invoice_service, engine, renderer


def _seed_contact(engine: object, *, complete_address: bool = True) -> int:
    with Session(engine) as session:
        category = ContactCategory(name="Interessent / Kunde", icon="handshake")
        contact = Contact(
            given_name="Alex",
            family_name="Meyer",
            organisation="Studio Nord",
            street="Hafenweg" if complete_address else None,
            house_number="8" if complete_address else None,
            postal_code="20457" if complete_address else None,
            city="Hamburg" if complete_address else None,
            country="DE" if complete_address else None,
            contact_category=category,
        )
        session.add(category)
        session.add(contact)
        session.commit()
        session.refresh(contact)
        return contact.id or 0


def _create_order(order_service: OrderService, *, contact_id: int) -> Order:
    order = order_service.create_order(contact_id=contact_id, sale_date=date(2026, 4, 10))
    order_service.save_order(
        order_id=order.id or 0,
        contact_id=contact_id,
        sale_date=date(2026, 4, 10),
        invoice_date=None,
        invoice_number=None,
        notes="Bitte ueberweisen.",
        items=[
            OrderItemInput(
                description="Originaldruck",
                quantity=Decimal("2.000"),
                unit_price_cents=12000,
                project_id=None,
                position=1,
            )
        ],
    )
    return order


def _fill_profile(invoice_service: InvoiceService) -> None:
    invoice_service.update_profile(
        display_name="Atelier Buddy Studio",
        street="Kanalstrasse",
        house_number="3a",
        address_extra="Atelier 2",
        postal_code="22767",
        city="Hamburg",
        country="DE",
        email="hallo@example.com",
        phone="+49 40 1234",
        website="https://example.com",
        tax_id_type="tax_number",
        tax_id_value="12/345/67890",
        bank_account_holder="Atelier Buddy Studio",
        iban="DE02100100109307118603",
        bic="PBNKDEFF",
        payment_term_days=14,
    )


def test_generate_invoice_assigns_number_date_and_generated_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    order_service, invoice_service, engine, renderer = _build_services(tmp_path, monkeypatch)
    contact_id = _seed_contact(engine)
    order = _create_order(order_service, contact_id=contact_id)
    _fill_profile(invoice_service)

    result = invoice_service.generate_invoice_document(order.id or 0)

    with Session(engine) as session:
        stored_order = session.exec(select(Order).where(Order.id == order.id)).first()

    assert stored_order is not None
    assert stored_order.invoice_date == date(2026, 4, 18)
    assert stored_order.invoice_number == "RE-2026-0001"
    assert stored_order.invoice_document_source == "generated"
    assert stored_order.invoice_document_updated_at is not None
    assert stored_order.invoice_document_uploaded_at is None
    assert Path(result.generated_document_path).exists()
    assert renderer.last_stylesheet_path is not None
    assert "RE-2026-0001" in renderer.last_html
    assert "§19 UStG" in renderer.last_html
    assert "Atelier Buddy Studio" in renderer.last_html
    assert "DE02100100109307118603" in renderer.last_html


def test_generate_invoice_uses_custom_template_when_selected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    order_service, invoice_service, engine, renderer = _build_services(tmp_path, monkeypatch)
    contact_id = _seed_contact(engine)
    order = _create_order(order_service, contact_id=contact_id)
    _fill_profile(invoice_service)
    custom_dir = invoice_service_module.settings.custom_invoice_template_dir
    custom_dir.mkdir(parents=True, exist_ok=True)
    (custom_dir / "invoice.html").write_text(
        "<html><body>custom:$invoice_number:$recipient_html:$items_html:$total_net:$logo_html</body></html>",
        encoding="utf-8",
    )
    (custom_dir / "invoice.css").write_text("body { font-family: sans-serif; }", encoding="utf-8")
    invoice_service.set_invoice_template_mode("custom")

    invoice_service.generate_invoice_document(order.id or 0)

    assert renderer.last_stylesheet_path == custom_dir / "invoice.css"
    assert renderer.last_base_url == str(custom_dir)
    assert "custom:RE-2026-0001" in renderer.last_html


def test_generate_invoice_rejects_missing_custom_template_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    order_service, invoice_service, engine, _ = _build_services(tmp_path, monkeypatch)
    contact_id = _seed_contact(engine)
    order = _create_order(order_service, contact_id=contact_id)
    _fill_profile(invoice_service)
    invoice_service.set_invoice_template_mode("custom")

    with pytest.raises(ValueError, match="invoice.html fehlt"):
        invoice_service.generate_invoice_document(order.id or 0)


def test_generate_invoice_does_not_fall_back_when_custom_rendering_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    order_service, invoice_service, engine, _ = _build_services(tmp_path, monkeypatch)
    failing_renderer = FailingRenderer()
    invoice_service._renderer = failing_renderer
    contact_id = _seed_contact(engine)
    order = _create_order(order_service, contact_id=contact_id)
    _fill_profile(invoice_service)
    custom_dir = invoice_service_module.settings.custom_invoice_template_dir
    custom_dir.mkdir(parents=True, exist_ok=True)
    (custom_dir / "invoice.html").write_text("<html><body>custom $invoice_number</body></html>", encoding="utf-8")
    (custom_dir / "invoice.css").write_text("body { color: black; }", encoding="utf-8")
    invoice_service.set_invoice_template_mode("custom")

    with pytest.raises(RuntimeError, match="kaputt"):
        invoice_service.generate_invoice_document(order.id or 0)

    assert failing_renderer.last_stylesheet_path == custom_dir / "invoice.css"


def test_generate_invoice_rejects_incomplete_invoice_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    order_service, invoice_service, engine, _ = _build_services(tmp_path, monkeypatch)
    contact_id = _seed_contact(engine)
    order = _create_order(order_service, contact_id=contact_id)

    with pytest.raises(ValueError, match="Rechnung kann nicht erzeugt werden"):
        invoice_service.generate_invoice_document(order.id or 0)


def test_generate_invoice_rejects_incomplete_contact_address(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    order_service, invoice_service, engine, _ = _build_services(tmp_path, monkeypatch)
    contact_id = _seed_contact(engine, complete_address=False)
    order = _create_order(order_service, contact_id=contact_id)
    _fill_profile(invoice_service)

    with pytest.raises(ValueError, match="Empfängeradresse"):
        invoice_service.generate_invoice_document(order.id or 0)


def test_generate_invoice_replaces_existing_document_and_removes_old_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    order_service, invoice_service, engine, _ = _build_services(tmp_path, monkeypatch)
    contact_id = _seed_contact(engine)
    order = _create_order(order_service, contact_id=contact_id)
    _fill_profile(invoice_service)

    old_document = storage.settings.order_invoices_dir / "legacy.pdf"
    old_document.parent.mkdir(parents=True, exist_ok=True)
    old_document.write_bytes(b"%PDF-legacy\n")
    order_service.set_invoice_document(
        order_id=order.id or 0,
        document_path=str(old_document),
        original_filename="legacy.pdf",
        source="uploaded",
    )

    result = invoice_service.generate_invoice_document(order.id or 0)

    assert result.replaced_document_path == str(old_document)
    assert not old_document.exists()
    assert Path(result.generated_document_path).exists()


def test_generate_invoice_uses_existing_manual_invoice_number_sequence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    order_service, invoice_service, engine, _ = _build_services(tmp_path, monkeypatch)
    contact_id = _seed_contact(engine)
    first = _create_order(order_service, contact_id=contact_id)
    second = _create_order(order_service, contact_id=contact_id)
    _fill_profile(invoice_service)

    order_service.save_order(
        order_id=first.id or 0,
        contact_id=contact_id,
        sale_date=date(2026, 4, 10),
        invoice_date=date(2026, 4, 12),
        invoice_number="RE-2026-0003",
        notes="Manuell vergeben",
        items=[
            OrderItemInput(
                description="Originaldruck",
                quantity=Decimal("1.000"),
                unit_price_cents=12000,
                project_id=None,
                position=1,
            )
        ],
    )

    result = invoice_service.generate_invoice_document(second.id or 0)

    with Session(engine) as session:
        stored_order = session.get(Order, second.id)

    assert stored_order is not None
    assert stored_order.invoice_number == "RE-2026-0004"
    assert result.order.invoice_number == "RE-2026-0004"
