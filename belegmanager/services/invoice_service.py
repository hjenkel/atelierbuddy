from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from html import escape
import multiprocessing
from pathlib import Path
from string import Template
from typing import Any, Protocol

from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from ..config import settings
from ..countries import COUNTRY_LABEL_BY_CODE, DEFAULT_CONTACT_COUNTRY_CODE
from ..db import engine
from ..models import Contact, InvoiceProfile, Order, OrderItem
from ..utils.storage import create_generated_order_invoice_path, safe_delete_file
from .order_service import order_item_total_cents, order_total_cents

INVOICE_PROFILE_SINGLETON_ID = 1
VALID_TAX_ID_TYPES = {"tax_number", "vat_id"}
INVOICE_TEMPLATE_MODE_STANDARD = "standard"
INVOICE_TEMPLATE_MODE_CUSTOM = "custom"
VALID_INVOICE_TEMPLATE_MODES = {INVOICE_TEMPLATE_MODE_STANDARD, INVOICE_TEMPLATE_MODE_CUSTOM}
MAX_PROFILE_FIELD_LENGTH = 255
MAX_PAYMENT_TERM_DAYS = 365
PDF_RENDER_TIMEOUT_SECONDS = 45


class InvoicePdfRenderer(Protocol):
    def render(
        self,
        html: str,
        *,
        stylesheet_path: Path,
        base_url: str,
        destination: Path,
    ) -> None: ...


class WeasyPrintInvoiceRenderer:
    def render(
        self,
        html: str,
        *,
        stylesheet_path: Path,
        base_url: str,
        destination: Path,
    ) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        ctx = multiprocessing.get_context("spawn")
        queue: Any = ctx.Queue()
        process = ctx.Process(
            target=_render_invoice_pdf_worker,
            args=(
                html,
                str(stylesheet_path),
                base_url,
                str(destination),
                queue,
            ),
        )
        process.start()
        process.join(timeout=PDF_RENDER_TIMEOUT_SECONDS)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
            destination.unlink(missing_ok=True)
            raise TimeoutError("PDF-Erzeugung hat das Zeitlimit überschritten")

        try:
            status, payload = queue.get_nowait()
        except Exception:
            if process.exitcode == 0 and destination.exists():
                return
            destination.unlink(missing_ok=True)
            raise RuntimeError("PDF-Erzeugung wurde unerwartet beendet")

        if status == "ok":
            return

        destination.unlink(missing_ok=True)
        raise RuntimeError(payload or "PDF-Erzeugung fehlgeschlagen")


def _render_invoice_pdf_worker(
    html: str,
    stylesheet_path: str,
    base_url: str,
    destination: str,
    queue: Any,
) -> None:
    try:
        from weasyprint import CSS, HTML
    except ImportError:
        queue.put(("error", "WeasyPrint ist nicht installiert. Bitte die PDF-Abhängigkeiten installieren."))
        return

    try:
        HTML(string=html, base_url=base_url).write_pdf(
            destination,
            stylesheets=[CSS(filename=stylesheet_path)],
        )
    except Exception as exc:
        queue.put(("error", str(exc)))
        return

    queue.put(("ok", None))


@dataclass(slots=True)
class InvoiceGenerationResult:
    order: Order
    generated_document_path: str
    replaced_document_path: str | None


class InvoiceService:
    def __init__(
        self,
        db_engine: Any = None,
        *,
        renderer: InvoicePdfRenderer | None = None,
        template_dir: Path | None = None,
        today_provider: Any | None = None,
    ) -> None:
        self._engine = db_engine or engine
        self._renderer = renderer or WeasyPrintInvoiceRenderer()
        self._standard_template_dir = template_dir or (settings.assets_dir / "invoice_templates" / "standard")
        self._today_provider = today_provider or date.today

    def get_profile(self) -> InvoiceProfile:
        with Session(self._engine) as session:
            return self._get_or_create_profile(session)

    def update_profile(
        self,
        *,
        display_name: str | None,
        street: str | None,
        house_number: str | None,
        address_extra: str | None,
        postal_code: str | None,
        city: str | None,
        country: str | None,
        email: str | None,
        phone: str | None,
        website: str | None,
        tax_id_type: str | None,
        tax_id_value: str | None,
        bank_account_holder: str | None,
        iban: str | None,
        bic: str | None,
        payment_term_days: int | str | None,
    ) -> InvoiceProfile:
        with Session(self._engine) as session:
            profile = self._get_or_create_profile(session)
            profile.display_name = self._normalize_text(display_name, label="Anzeigename")
            profile.street = self._normalize_text(street, label="Straße")
            profile.house_number = self._normalize_text(house_number, label="Hausnummer")
            profile.address_extra = self._normalize_text(address_extra, label="Adresszusatz")
            profile.postal_code = self._normalize_text(postal_code, label="PLZ")
            profile.city = self._normalize_text(city, label="Ort")
            profile.country = self._normalize_country(country)
            profile.email = self._normalize_text(email, label="E-Mail")
            profile.phone = self._normalize_text(phone, label="Telefon")
            profile.website = self._normalize_text(website, label="Website")
            profile.tax_id_type = self._normalize_tax_id_type(tax_id_type)
            profile.tax_id_value = self._normalize_text(tax_id_value, label="Steuerkennzeichen")
            profile.bank_account_holder = self._normalize_text(bank_account_holder, label="Kontoinhaber")
            profile.iban = self._normalize_text(iban, label="IBAN")
            profile.bic = self._normalize_text(bic, label="BIC")
            profile.payment_term_days = self._normalize_payment_term_days(payment_term_days)
            profile.updated_at = datetime.now(timezone.utc)
            session.add(profile)
            session.commit()
            session.refresh(profile)
            return profile

    def set_invoice_template_mode(self, mode: str | None) -> InvoiceProfile:
        normalized_mode = self._normalize_invoice_template_mode(mode)
        with Session(self._engine) as session:
            profile = self._get_or_create_profile(session)
            profile.invoice_template_mode = normalized_mode
            profile.updated_at = datetime.now(timezone.utc)
            session.add(profile)
            session.commit()
            session.refresh(profile)
            return profile

    def custom_template_status(self) -> dict[str, Any]:
        template_dir = settings.custom_invoice_template_dir
        html_path = template_dir / "invoice.html"
        css_path = template_dir / "invoice.css"
        fonts_dir = settings.custom_invoice_fonts_dir
        font_count = 0
        if fonts_dir.exists():
            font_count = len([path for path in fonts_dir.iterdir() if path.is_file()])
        return {
            "html_path": html_path,
            "css_path": css_path,
            "html_exists": html_path.exists() and html_path.is_file(),
            "css_exists": css_path.exists() and css_path.is_file(),
            "font_count": font_count,
            "complete": html_path.exists() and html_path.is_file() and css_path.exists() and css_path.is_file(),
        }

    def set_logo_path(self, logo_path: str | None) -> InvoiceProfile:
        normalized_logo_path = self._normalize_text(logo_path, label="Logo-Pfad")
        with Session(self._engine) as session:
            profile = self._get_or_create_profile(session)
            profile.logo_path = normalized_logo_path
            profile.updated_at = datetime.now(timezone.utc)
            session.add(profile)
            session.commit()
            session.refresh(profile)
            return profile

    def clear_logo_path(self) -> str | None:
        with Session(self._engine) as session:
            profile = self._get_or_create_profile(session)
            old_logo_path = profile.logo_path
            profile.logo_path = None
            profile.updated_at = datetime.now(timezone.utc)
            session.add(profile)
            session.commit()
            return old_logo_path

    def collect_generation_issues(self, order_id: int) -> list[str]:
        with Session(self._engine) as session:
            order = self._load_order(session, order_id)
            profile = self._get_or_create_profile(session)
            return self._collect_generation_issues(order=order, profile=profile)

    def generate_invoice_document(self, order_id: int) -> InvoiceGenerationResult:
        destination: Path | None = None
        with Session(self._engine) as session:
            order = self._load_order(session, order_id)
            profile = self._get_or_create_profile(session)

            issues = self._collect_generation_issues(order=order, profile=profile)
            if issues:
                raise ValueError("Rechnung kann nicht erzeugt werden: " + "; ".join(issues))
            template_dir = self._template_dir_for_profile(profile)

            effective_invoice_date = order.invoice_date or self._today_provider()
            effective_invoice_number = (order.invoice_number or "").strip() or self._next_invoice_number(
                session,
                invoice_year=effective_invoice_date.year,
            )
            self._ensure_invoice_number_available(session, effective_invoice_number, order.id or 0)

            destination = create_generated_order_invoice_path(order.id or 0, effective_invoice_number)
            html = self._build_invoice_html(
                order=order,
                profile=profile,
                invoice_date=effective_invoice_date,
                invoice_number=effective_invoice_number,
                template_dir=template_dir,
            )
            stylesheet_path = template_dir / "invoice.css"
            self._renderer.render(
                html,
                stylesheet_path=stylesheet_path,
                base_url=str(template_dir),
                destination=destination,
            )

            replaced_document_path = order.invoice_document_path
            order.invoice_date = effective_invoice_date
            order.invoice_number = effective_invoice_number
            order.invoice_document_path = str(destination)
            order.invoice_document_original_filename = f"{effective_invoice_number}.pdf"
            order.invoice_document_updated_at = datetime.now(timezone.utc)
            order.invoice_document_source = "generated"
            order.updated_at = datetime.now(timezone.utc)
            session.add(order)
            session.commit()
            session.refresh(order)
            safe_delete_file(replaced_document_path if replaced_document_path != str(destination) else None)
            return InvoiceGenerationResult(
                order=order,
                generated_document_path=str(destination),
                replaced_document_path=replaced_document_path,
            )

    def _load_order(self, session: Session, order_id: int) -> Order:
        order = session.exec(
            select(Order)
            .where(Order.id == order_id)
            .options(
                selectinload(Order.contact),
                selectinload(Order.items).selectinload(OrderItem.project),
            )
        ).first()
        if order is None:
            raise ValueError("Verkauf nicht gefunden")
        if order.deleted_at is not None:
            raise ValueError("Gelöschter Verkauf kann nicht fakturiert werden")
        return order

    def _get_or_create_profile(self, session: Session) -> InvoiceProfile:
        profile = session.get(InvoiceProfile, INVOICE_PROFILE_SINGLETON_ID)
        if profile is not None:
            return profile
        profile = InvoiceProfile(id=INVOICE_PROFILE_SINGLETON_ID)
        session.add(profile)
        session.commit()
        session.refresh(profile)
        return profile

    def _collect_generation_issues(self, *, order: Order, profile: InvoiceProfile) -> list[str]:
        issues: list[str] = []
        issues.extend(self._profile_issues(profile))
        issues.extend(self._recipient_issues(order.contact))
        issues.extend(self._template_issues(profile))
        if not order.items:
            issues.append("Mindestens eine Position fehlt")
        return issues

    def _profile_issues(self, profile: InvoiceProfile) -> list[str]:
        issues: list[str] = []
        required_fields = {
            "Anzeigename": profile.display_name,
            "Straße": profile.street,
            "Hausnummer": profile.house_number,
            "PLZ": profile.postal_code,
            "Ort": profile.city,
            "Land": profile.country,
            "Steuerkennzeichen": profile.tax_id_value,
            "Kontoinhaber": profile.bank_account_holder,
            "IBAN": profile.iban,
            "BIC": profile.bic,
        }
        for label, value in required_fields.items():
            if not (value or "").strip():
                issues.append(f"{label} im Rechnungssteller fehlt")
        if profile.tax_id_type not in VALID_TAX_ID_TYPES:
            issues.append("Steuerkennzeichen-Typ im Rechnungssteller ist ungültig")
        if profile.payment_term_days is None:
            issues.append("Zahlungsziel im Rechnungssteller fehlt")
        return issues

    def _template_issues(self, profile: InvoiceProfile) -> list[str]:
        mode = self._normalize_invoice_template_mode(profile.invoice_template_mode)
        if mode == INVOICE_TEMPLATE_MODE_STANDARD:
            return []
        status = self.custom_template_status()
        issues: list[str] = []
        if not status["html_exists"]:
            issues.append("Eigene Rechnungsvorlage: invoice.html fehlt")
        if not status["css_exists"]:
            issues.append("Eigene Rechnungsvorlage: invoice.css fehlt")
        return issues

    def _template_dir_for_profile(self, profile: InvoiceProfile) -> Path:
        mode = self._normalize_invoice_template_mode(profile.invoice_template_mode)
        if mode == INVOICE_TEMPLATE_MODE_CUSTOM:
            return settings.custom_invoice_template_dir
        return self._standard_template_dir

    def _recipient_issues(self, contact: Contact | None) -> list[str]:
        if contact is None:
            return ["Kontakt fehlt"]
        issues: list[str] = []
        if not self._contact_heading_lines(contact):
            issues.append("Empfängername fehlt")
        required_fields = {
            "Straße": contact.street,
            "Hausnummer": contact.house_number,
            "PLZ": contact.postal_code,
            "Ort": contact.city,
            "Land": contact.country,
        }
        for label, value in required_fields.items():
            if not (value or "").strip():
                issues.append(f"{label} in der Empfängeradresse fehlt")
        return issues

    def _next_invoice_number(self, session: Session, *, invoice_year: int) -> str:
        prefix = f"RE-{invoice_year}-"
        existing_numbers = session.exec(select(Order.invoice_number).where(Order.invoice_number.startswith(prefix))).all()
        max_sequence = 0
        for value in existing_numbers:
            normalized = (value or "").strip()
            if not normalized.startswith(prefix):
                continue
            sequence_part = normalized[len(prefix) :]
            if sequence_part.isdigit():
                max_sequence = max(max_sequence, int(sequence_part))
        return f"{prefix}{max_sequence + 1:04d}"

    def _ensure_invoice_number_available(self, session: Session, invoice_number: str, order_id: int) -> None:
        existing = session.exec(
            select(Order).where(
                Order.invoice_number == invoice_number,
                Order.id != order_id,
            )
        ).first()
        if existing is not None:
            raise ValueError("Rechnungsnummer existiert bereits")

    def _build_invoice_html(
        self,
        *,
        order: Order,
        profile: InvoiceProfile,
        invoice_date: date,
        invoice_number: str,
        template_dir: Path | None = None,
    ) -> str:
        active_template_dir = template_dir or self._standard_template_dir
        template = Template((active_template_dir / "invoice.html").read_text(encoding="utf-8"))
        custom_font_face_css = self._render_custom_font_face_css()
        logo_uri = ""
        if profile.logo_path:
            logo_path = Path(profile.logo_path)
            if logo_path.exists():
                logo_uri = logo_path.resolve().as_uri()

        sender_address_lines = self._sender_address_lines(profile)
        recipient_lines = self._recipient_address_lines(order.contact)
        payment_due_date = invoice_date + timedelta(days=int(profile.payment_term_days or 0))
        total_cents = order_total_cents(order.items)
        street_line = " ".join(part for part in (profile.street or "", profile.house_number or "") if part.strip())
        city_line = " ".join(part for part in (profile.postal_code or "", profile.city or "") if part.strip())

        items_html = "".join(
            self._render_item_row(
                position=index,
                item=item,
                currency=settings.default_currency,
            )
            for index, item in enumerate(sorted(order.items, key=lambda current: current.position), start=1)
        )
        tax_label = "USt-IdNr." if profile.tax_id_type == "vat_id" else "Steuernummer"
        tax_label_footer = "USt-Id" if profile.tax_id_type == "vat_id" else "Steuer-ID"

        return template.safe_substitute(
            {
                "custom_font_face_css": custom_font_face_css,
                "logo_html": (
                    f'<img class="invoice-logo" src="{escape(logo_uri)}" alt="Logo" />'
                    if logo_uri
                    else '<div class="invoice-logo invoice-logo--placeholder"></div>'
                ),
                "sender_name": escape(profile.display_name or ""),
                "sender_address_html": "".join(f"<div>{escape(line)}</div>" for line in sender_address_lines),
                "sender_street_line": escape(street_line),
                "sender_city_line": escape(city_line),
                "sender_contact_html": self._render_sender_contact(profile),
                "recipient_html": "".join(f"<div>{escape(line)}</div>" for line in recipient_lines),
                "invoice_number": escape(invoice_number),
                "invoice_date": escape(self._format_date(invoice_date)),
                "sale_date": escape(self._format_date(order.sale_date)),
                "payment_due_date": escape(self._format_date(payment_due_date)),
                "tax_label": escape(tax_label),
                "tax_label_footer": escape(tax_label_footer),
                "tax_value": escape(profile.tax_id_value or ""),
                "items_html": items_html,
                "total_net": escape(self._format_currency(total_cents, settings.default_currency)),
                "payment_term_days": escape(str(profile.payment_term_days or "")),
                "bank_account_holder": escape(profile.bank_account_holder or ""),
                "iban": escape(profile.iban or ""),
                "bic": escape(profile.bic or ""),
                "currency": escape(settings.default_currency),
                "notes": escape((order.notes or "").strip()),
            }
        )

    def _render_item_row(self, *, position: int, item: OrderItem, currency: str) -> str:
        line_total = order_item_total_cents(item.quantity, item.unit_price_cents)
        return (
            "<tr>"
            f"<td>{position}</td>"
            f"<td>{escape(item.description)}</td>"
            f"<td class=\"align-right\">{escape(self._format_quantity(item.quantity))}</td>"
            f"<td class=\"align-right\">{escape(self._format_currency(item.unit_price_cents, currency))}</td>"
            f"<td class=\"align-right\">{escape(self._format_currency(line_total, currency))}</td>"
            "</tr>"
        )

    def _render_custom_font_face_css(self) -> str:
        custom_fonts_dir = settings.data_dir / "customfonts"
        candidate_paths = [
            custom_fonts_dir / "invoice-display.ttf",
            custom_fonts_dir / "FascinateInline-Regular.ttf",
        ]
        font_path = next((path for path in candidate_paths if path.exists()), None)
        if font_path is None:
            return ""
        font_uri = escape(font_path.resolve().as_uri())
        return (
            "@font-face {\n"
            '  font-family: "Invoice Display";\n'
            f'  src: url("{font_uri}") format("truetype");\n'
            "  font-style: normal;\n"
            "  font-weight: 400;\n"
            "}\n"
        )

    def _render_sender_contact(self, profile: InvoiceProfile) -> str:
        lines: list[str] = []
        if (profile.email or "").strip():
            lines.append(f"E-Mail: {profile.email.strip()}")
        if (profile.phone or "").strip():
            lines.append(f"Telefon: {profile.phone.strip()}")
        if (profile.website or "").strip():
            lines.append(profile.website.strip())
        return "".join(f"<div>{escape(line)}</div>" for line in lines)

    def _profile_address_lines(self, profile: InvoiceProfile) -> list[str]:
        street_line = " ".join(part for part in (profile.street or "", profile.house_number or "") if part.strip())
        city_line = " ".join(part for part in (profile.postal_code or "", profile.city or "") if part.strip())
        lines = [profile.display_name or "", street_line, profile.address_extra or "", city_line, self._country_label(profile.country)]
        return [line.strip() for line in lines if line and line.strip()]

    def _sender_address_lines(self, profile: InvoiceProfile) -> list[str]:
        street_line = " ".join(part for part in (profile.street or "", profile.house_number or "") if part.strip())
        city_line = " ".join(part for part in (profile.postal_code or "", profile.city or "") if part.strip())
        lines = [street_line, profile.address_extra or "", city_line, self._country_label(profile.country)]
        return [line.strip() for line in lines if line and line.strip()]

    def _recipient_address_lines(self, contact: Contact | None) -> list[str]:
        if contact is None:
            return []
        street_line = " ".join(part for part in (contact.street or "", contact.house_number or "") if part.strip())
        city_line = " ".join(part for part in (contact.postal_code or "", contact.city or "") if part.strip())
        lines = self._contact_heading_lines(contact)
        lines.extend(
            line.strip()
            for line in (street_line, contact.address_extra or "", city_line, self._country_label(contact.country))
            if line and line.strip()
        )
        return lines

    def _contact_heading_lines(self, contact: Contact) -> list[str]:
        lines: list[str] = []
        if (contact.organisation or "").strip():
            lines.append(contact.organisation.strip())
        person_name = " ".join(part for part in (contact.given_name or "", contact.family_name or "") if part.strip())
        if person_name:
            lines.append(person_name)
        return lines

    def _country_label(self, country_code: str | None) -> str:
        code = (country_code or DEFAULT_CONTACT_COUNTRY_CODE).strip().upper()
        return COUNTRY_LABEL_BY_CODE.get(code, code)

    def _normalize_text(self, value: str | None, *, label: str) -> str | None:
        normalized = (value or "").strip()
        if not normalized:
            return None
        if len(normalized) > MAX_PROFILE_FIELD_LENGTH:
            raise ValueError(f"{label} darf maximal {MAX_PROFILE_FIELD_LENGTH} Zeichen lang sein")
        return normalized

    def _normalize_country(self, value: str | None) -> str:
        normalized = (value or DEFAULT_CONTACT_COUNTRY_CODE).strip().upper() or DEFAULT_CONTACT_COUNTRY_CODE
        if len(normalized) != 2:
            raise ValueError("Land ist ungültig")
        return normalized

    def _normalize_tax_id_type(self, value: str | None) -> str:
        normalized = (value or "tax_number").strip().lower()
        if normalized not in VALID_TAX_ID_TYPES:
            raise ValueError("Steuerkennzeichen-Typ ist ungültig")
        return normalized

    def _normalize_invoice_template_mode(self, value: str | None) -> str:
        normalized = (value or INVOICE_TEMPLATE_MODE_STANDARD).strip().lower()
        if normalized not in VALID_INVOICE_TEMPLATE_MODES:
            raise ValueError("Rechnungsvorlage ist ungültig")
        return normalized

    def _normalize_payment_term_days(self, value: int | str | None) -> int | None:
        raw = "" if value is None else str(value).strip()
        if not raw:
            return None
        try:
            parsed = int(raw)
        except ValueError as exc:
            raise ValueError("Zahlungsziel ist ungültig") from exc
        if parsed <= 0 or parsed > MAX_PAYMENT_TERM_DAYS:
            raise ValueError(f"Zahlungsziel muss zwischen 1 und {MAX_PAYMENT_TERM_DAYS} Tagen liegen")
        return parsed

    def _format_currency(self, cents: int, currency: str) -> str:
        amount = Decimal(cents) / Decimal("100")
        formatted = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{formatted} {currency}"

    def _format_quantity(self, value: Decimal) -> str:
        normalized = format(value, "f").rstrip("0").rstrip(".")
        return (normalized or "0").replace(".", ",")

    def _format_date(self, value: date) -> str:
        return value.strftime("%d.%m.%Y")
