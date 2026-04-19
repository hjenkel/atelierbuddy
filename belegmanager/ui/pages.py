from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable

from nicegui import context, events, ui
from sqlalchemy import func, or_
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from ..app_state import ServiceContainer
from ..config import settings
from ..constants import (
    CONTACT_CATEGORY_ICON_OPTIONS,
    COST_ALLOCATION_STATUS_DRAFT,
    COST_ALLOCATION_STATUS_POSTED,
    COST_TYPE_ICON_OPTIONS,
    DEFAULT_CONTACT_CATEGORY_ICON,
    DEFAULT_CONTACT_CATEGORY_NAME,
    DEFAULT_COST_TYPE_ICON,
)
from ..countries import COUNTRY_OPTION_MAP, COUNTRY_LABEL_BY_CODE, DEFAULT_CONTACT_COUNTRY_CODE
from ..db import engine
from ..models import Contact, ContactCategory, CostAllocation, CostSubcategory, CostType, Order, OrderItem, Project, Receipt, Supplier
from ..receipt_completion import ReceiptCompletionService
from ..schemas import AllocationInput, OrderItemInput, ReceiptSaveInput
from ..services.order_service import (
    QUANTITY_STEP,
    order_item_total_cents,
    order_status_key,
    order_status_label,
    order_total_cents,
)
from ..utils.storage import (
    is_supported_filename,
    save_uploaded_invoice_logo,
    save_uploaded_order_invoice,
    save_uploaded_work_cover,
    safe_delete_file,
    to_files_url,
)

DOC_TYPE_INVOICE = "invoice"
DOC_TYPE_CREDIT_NOTE = "credit_note"
INVOICE_GENERATION_FEEDBACK_TIMEOUT_SECONDS = 8
INVOICE_GENERATION_MAX_WAIT_SECONDS = 65
LOG = logging.getLogger(__name__)
_NAV_STATE = {
    "sidebar_expanded": True,
    "sidebar_mobile_open": False,
    "open_groups": {"finance": True, "management": True},
    "last_path": None,
    "flash_notification": None,
    "receipt_return_path": None,
}

_NAV_CONFIG: list[dict[str, Any]] = [
    {"type": "item", "path": "/", "label": "Dashboard", "icon": "dashboard"},
    {"type": "item", "path": "/kontakte", "label": "Kontakte", "icon": "contacts"},
    {"type": "item", "path": "/projekte", "label": "Projekte", "icon": "palette"},
    {
        "type": "group",
        "key": "finance",
        "label": "Finanzen",
        "icon": "account_balance_wallet",
        "items": [
            {"path": "/verkaeufe", "label": "Verkäufe", "icon": "receipt_long"},
            {"path": "/belege", "label": "Belege", "icon": "description"},
            {"path": "/auswertung", "label": "Auswertung", "icon": "insights"},
        ],
    },
    {
        "type": "group",
        "key": "management",
        "label": "Verwaltung",
        "icon": "admin_panel_settings",
        "items": [
            {"path": "/lieferanten", "label": "Anbieter", "icon": "local_shipping"},
            {"path": "/kategorien", "label": "Kostenkategorien", "icon": "category"},
            {"path": "/kontaktkategorien", "label": "Kontaktkategorien", "icon": "badge"},
        ],
    },
]

_HELP_CONTENT_BY_PATH: dict[str, dict[str, str]] = {
    "/": {
        "title": "Hilfe · Dashboard",
        "body": "Hier siehst du, welche Belege als Nächstes ergänzt werden sollten. "
        "Klicke auf ein Thumbnail, um direkt in die Detailerfassung zu springen.",
    },
    "/belege": {
        "title": "Hilfe · Belege",
        "body": "Hier landen deine Rechnungen, Bons, Gutschriften und sonstigen Belege. "
        "Du hältst fest, was finanziell passiert ist, und schaffst damit die Grundlage "
        "für Überblick, Ordnung und spätere Auswertungen.",
    },
    "/verkaeufe": {
        "title": "Hilfe · Verkäufe",
        "body": "Hier pflegst du deine Verkäufe und Ausgangsrechnungen mit Positionen, Rechnungsdaten "
        "und optionalem Rechnungsdokument. Als abgerechnet gilt ein Verkauf erst, wenn Rechnungsdatum, "
        "Rechnungsnummer und Dokument vorhanden sind.",
    },
    "/projekte": {
        "title": "Hilfe · Projekte",
        "body": "Hier verwaltest du deine Werke, Ausstellungen und Aufträge. Projekte sind "
        "konkrete Vorhaben mit klarem Bezug. Wenn kein Projekt gewählt wird, läuft die "
        "Zuordnung automatisch als allgemeine Ausgabe.",
    },
    "/kontakte": {
        "title": "Hilfe · Kontakte",
        "body": "Hier pflegst du Menschen, mit denen du arbeitest oder arbeiten möchtest. "
        "Die Kontaktverwaltung bleibt bewusst schlank und hilft dir beim schnellen Wiederfinden "
        "von Ansprechpartnern ohne CRM-Überbau.",
    },
    "/lieferanten": {
        "title": "Hilfe · Anbieter",
        "body": "Hier sammelst du Firmen, Shops, Dienstleister und Vermieter, von denen deine "
        "Belege kommen. Das spart Tipparbeit und sorgt dafür, dass wiederkehrende Angaben "
        "nicht jedes Mal neu zusammengesucht werden müssen.",
    },
    "/kontaktkategorien": {
        "title": "Hilfe · Kontaktkategorien",
        "body": "Hier ordnest du Kontakte grob ein, zum Beispiel als Interessent, Veranstalter "
        "oder Presse. Die Kategorien bleiben absichtlich einfach und ohne Unterkategorien.",
    },
    "/kategorien": {
        "title": "Hilfe · Kostenkategorien",
        "body": "Hier ordnest du ein, um welche Art von Ausgabe es sich handelt, zum Beispiel "
        "Material, Software oder Miete. Das bringt Struktur in deine Ausgaben und ist später "
        "wichtig für die Auswertung in der EÜR.",
    },
    "/auswertung": {
        "title": "Hilfe · Auswertung",
        "body": "Hier bekommst du eine tabellarische Finanzübersicht für den gewählten Zeitraum: "
        "Gesamtsumme, Kostenkategorien und Drilldown bis zu den Unterkategorien.",
    },
    "/kostenbereiche": {
        "title": "Hilfe · Kostenstellen",
        "body": "Dieser Bereich ist aktuell deaktiviert. Die Zuordnung zur Standard-Kostenstelle "
        "läuft im Hintergrund automatisch, wenn kein Projekt gewählt ist.",
    },
    "/einstellungen": {
        "title": "Hilfe · Einstellungen",
        "body": "Hier pflegst du den installweiten Rechnungssteller fuer automatische Rechnungen "
        "und findest zusaetzlich technische Informationen zu deinem lokalen Setup.",
    },
}

_DEFAULT_HELP_CONTENT = {
    "title": "Hilfe",
    "body": "Hier findest du Kontext zur aktuellen Seite. Weitere Hilfefunktionen können später ergänzt werden.",
}


def _notify_error(user_message: str, exc: Exception) -> None:
    if isinstance(exc, ValueError):
        ui.notify(f"{user_message}: {exc}", type="negative")
        return
    error_id = uuid.uuid4().hex[:8]
    LOG.exception("UI action failed (%s): %s", error_id, user_message)
    ui.notify(f"{user_message}. Fehler-ID: {error_id}", type="negative")


def _notify_client(client: Any, message: str, *, type: str = "info") -> None:
    try:
        client.outbox.enqueue_message(
            "notify",
            {
                "message": str(message),
                "type": type,
                "position": "bottom",
            },
            client.id,
        )
    except Exception:
        LOG.debug("Client-Benachrichtigung konnte nicht gesendet werden", exc_info=True)


def _run_client_javascript(client: Any, code: str) -> None:
    try:
        client.run_javascript(code)
    except Exception:
        LOG.debug("Client-JavaScript konnte nicht gesendet werden", exc_info=True)


async def _await_client_javascript(client: Any, code: str, *, timeout: float = 1.0) -> Any | None:
    try:
        return await client.run_javascript(code, timeout=timeout)
    except Exception:
        LOG.debug("Client-JavaScript konnte nicht abgeschlossen werden", exc_info=True)
        return None


async def _flush_active_input(client: Any, *, settle_ms: int = 40) -> None:
    await _await_client_javascript(
        client,
        """
        (() => {
          const active = document.activeElement;
          if (active && typeof active.blur === 'function') {
            active.blur();
          }
          return true;
        })()
        """,
        timeout=1.0,
    )
    await asyncio.sleep(settle_ms / 1000)


def _notify_error_with_client(client: Any, user_message: str, exc: Exception) -> None:
    if isinstance(exc, ValueError):
        _notify_client(client, f"{user_message}: {exc}", type="negative")
        return
    error_id = uuid.uuid4().hex[:8]
    LOG.exception("UI action failed (%s): %s", error_id, user_message)
    _notify_client(client, f"{user_message}. Fehler-ID: {error_id}", type="negative")


def _queue_flash_notification(message: str, *, type: str = "info") -> None:
    _NAV_STATE["flash_notification"] = {"message": str(message), "type": str(type)}


def _consume_flash_notification() -> dict[str, str] | None:
    payload = _NAV_STATE.get("flash_notification")
    _NAV_STATE["flash_notification"] = None
    if not isinstance(payload, dict):
        return None
    message = str(payload.get("message") or "").strip()
    if not message:
        return None
    return {
        "message": message,
        "type": str(payload.get("type") or "info"),
    }


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _to_int_list(values: list[object] | None) -> list[int]:
    if not values:
        return []
    result: list[int] = []
    for value in values:
        try:
            result.append(int(value))
        except (TypeError, ValueError):
            continue
    return result


def _human_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def _contact_display_name_from_values(given_name: str | None, family_name: str | None) -> str:
    parts = [part.strip() for part in (given_name, family_name) if (part or "").strip()]
    return " ".join(parts) if parts else "-"


def _contact_display_name(contact: Contact) -> str:
    return _contact_display_name_from_values(contact.given_name, contact.family_name)


def _contact_sort_key(contact: Contact) -> tuple[str, str, str]:
    family_name = (contact.family_name or "").strip().casefold()
    given_name = (contact.given_name or "").strip().casefold()
    organisation = (contact.organisation or "").strip().casefold()
    primary = family_name or given_name
    secondary = given_name if family_name else ""
    return (primary, secondary, organisation)


def _contact_country_label(country_code: str | None) -> str:
    code = (country_code or "").strip().upper()
    if not code:
        code = DEFAULT_CONTACT_COUNTRY_CODE
    return COUNTRY_LABEL_BY_CODE.get(code, code)


def _contact_location_label(contact: Contact) -> str:
    parts = [part.strip() for part in (contact.postal_code, contact.city) if (part or "").strip()]
    location = " ".join(parts)
    country = _contact_country_label(contact.country)
    if location and country:
        return f"{location}, {country}"
    return location or country or "-"


def _parse_money_to_cents(
    value: str | int | float | Decimal | None,
    *,
    allow_negative: bool = False,
) -> int | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        text = format(value, "f")
    elif isinstance(value, (int, float)):
        text = str(value)
    else:
        text = str(value)
    text = text.strip()
    if not text:
        return None
    cleaned = (
        text.replace("€", "")
        .replace(settings.default_currency, "")
        .replace(" ", "")
        .replace("\u00a0", "")
        .strip()
    )
    if not cleaned:
        return None
    sign = 1
    if cleaned.startswith("-"):
        sign = -1
        cleaned = cleaned[1:].strip()
    elif cleaned.startswith("+"):
        cleaned = cleaned[1:].strip()
    if "-" in cleaned or "+" in cleaned:
        raise ValueError("Ungültiger Betrag")
    if sign < 0 and not allow_negative:
        raise ValueError("Betrag darf nicht negativ sein")
    if not cleaned:
        return None

    filtered = "".join(ch for ch in cleaned if ch.isdigit() or ch in {".", ","})
    if not filtered or not any(ch.isdigit() for ch in filtered):
        raise ValueError("Ungültiger Betrag")

    has_dot = "." in filtered
    has_comma = "," in filtered
    if has_dot and has_comma:
        last_dot = filtered.rfind(".")
        last_comma = filtered.rfind(",")
        separator_idx = max(last_dot, last_comma)
        integer_part = filtered[:separator_idx].replace(".", "").replace(",", "")
        fraction_part = filtered[separator_idx + 1 :].replace(".", "").replace(",", "")
        integer_part = integer_part or "0"
        normalized = f"{integer_part}.{fraction_part}" if fraction_part else integer_part
    elif has_dot or has_comma:
        sep = "." if has_dot else ","
        parts = filtered.split(sep)
        if len(parts) == 1:
            normalized = parts[0]
        else:
            head = "".join(parts[:-1])
            tail = parts[-1]
            head = head or "0"
            if tail == "":
                normalized = head
            elif len(tail) in {1, 2}:
                normalized = f"{head}.{tail}"
            elif len(tail) == 3 and all(part.isdigit() for part in parts):
                normalized = "".join(parts)
            else:
                normalized = f"{head}.{tail}"
    else:
        normalized = filtered

    try:
        amount = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError("Ungültiger Betrag") from exc
    if amount < 0:
        raise ValueError("Betrag darf nicht negativ sein")
    cents = (amount * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(cents) * sign


def _allocation_total_and_diff_cents(
    gross_cents: int | None,
    amount_values: list[str | int | float | Decimal | None],
    *,
    allow_negative: bool = False,
) -> tuple[int, int | None]:
    total = 0
    for value in amount_values:
        try:
            amount_cents = _parse_money_to_cents(value, allow_negative=allow_negative)
        except ValueError:
            amount_cents = None
        if amount_cents is not None:
            total += amount_cents
    if gross_cents is None:
        return total, None
    return total, gross_cents - total


def _format_cents(cents: int | None, currency: str = "EUR") -> str:
    if cents is None:
        return "-"
    value = (Decimal(cents) / Decimal("100")).quantize(Decimal("0.01"))
    normalized = format(value, "f").replace(".", ",")
    return f"{normalized} {currency}"


def _format_percent(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}".replace(".", ",")


def _format_cents_input(cents: int | None) -> str:
    if cents is None:
        return ""
    value = (Decimal(cents) / Decimal("100")).quantize(Decimal("0.01"))
    return format(value, "f").replace(".", ",")


def _parse_quantity(value: str | int | float | Decimal | None) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        quantity = value
    else:
        text = str(value).strip().replace(" ", "").replace("\u00a0", "").replace(",", ".")
        if not text:
            return None
        try:
            quantity = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError("Ungültige Menge") from exc
    if quantity.as_tuple().exponent < -3:
        raise ValueError("Menge darf maximal 3 Nachkommastellen haben")
    return quantity.quantize(QUANTITY_STEP)


def _format_quantity(value: Decimal | None) -> str:
    if value is None:
        return ""
    normalized = format(value.quantize(QUANTITY_STEP), "f")
    normalized = normalized.rstrip("0").rstrip(".")
    return (normalized or "0").replace(".", ",")


def _normalize_quantity_input(value: str | int | float | Decimal | None) -> str:
    quantity = _parse_quantity(value)
    return _format_quantity(quantity)


def _normalize_money_input(
    value: str | int | float | Decimal | None,
    *,
    allow_negative: bool = False,
) -> str:
    cents = _parse_money_to_cents(value, allow_negative=allow_negative)
    return _format_cents_input(cents)


def _project_values_from_rows(rows: list[dict[str, Any]]) -> set[int | None]:
    return {int(value) if isinstance(value, int) else None for value in (row.get("project_id") for row in rows)}


def _common_project_id_from_rows(rows: list[dict[str, Any]]) -> int | None:
    project_values = _project_values_from_rows(rows)
    if len(project_values) != 1:
        return None
    only_value = next(iter(project_values))
    return only_value if isinstance(only_value, int) else None


def _uses_position_project_mode(rows: list[dict[str, Any]]) -> bool:
    return len(_project_values_from_rows(rows)) > 1


def _compute_net_cents(gross_cents: int | None, vat_rate_percent: float | None) -> int | None:
    if gross_cents is None:
        return None
    rate = vat_rate_percent if vat_rate_percent is not None else 0.0
    divisor = Decimal("1") + (Decimal(str(rate)) / Decimal("100"))
    net = (Decimal(gross_cents) / divisor).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(net)


def _extract_row_id(args: Any) -> int | None:
    def to_int(value: Any) -> int | None:
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            try:
                return int(value.strip())
            except ValueError:
                return None
        return None

    def resolve_id(value: Any, depth: int = 0) -> int | None:
        if depth > 5:
            return None

        direct = to_int(value)
        if direct is not None:
            return direct

        if isinstance(value, str):
            text = value.strip()
            if text.startswith("{") or text.startswith("["):
                try:
                    parsed = json.loads(text)
                except Exception:
                    return None
                return resolve_id(parsed, depth + 1)
            return None

        if isinstance(value, dict):
            # Strictly prioritize explicit id-like fields.
            for key in ("id", "key"):
                candidate = to_int(value.get(key))
                if candidate is not None:
                    return candidate

            # Follow typical wrappers used by NiceGUI/Quasar.
            for key in ("row", "args", "value", "data", "payload"):
                if key in value:
                    candidate = resolve_id(value.get(key), depth + 1)
                    if candidate is not None:
                        return candidate

            # As a safe fallback, only recurse into structured children,
            # never scalar numbers from raw browser events (e.g. "detail": 1).
            for child in value.values():
                if isinstance(child, (dict, list, tuple, str)):
                    candidate = resolve_id(child, depth + 1)
                    if candidate is not None:
                        return candidate
            return None

        if isinstance(value, (list, tuple)):
            # QTable rowClick is typically [event, row, pageIndex]:
            # try row payload first.
            if len(value) >= 2:
                candidate = resolve_id(value[1], depth + 1)
                if candidate is not None:
                    return candidate
            for child in value:
                if isinstance(child, (dict, list, tuple, str)):
                    candidate = resolve_id(child, depth + 1)
                    if candidate is not None:
                        return candidate
            return None

        return None

    payload = args.args if hasattr(args, "args") else args
    return resolve_id(payload)


def _extract_model_value(event: Any, fallback: Any = None) -> Any:
    payload = event.args if hasattr(event, "args") else event
    if isinstance(payload, dict):
        for key in ("value", "modelValue", "model-value"):
            if key in payload:
                return payload.get(key)
        return fallback
    if isinstance(payload, (list, tuple)):
        return payload[0] if payload else fallback
    return payload if payload is not None else fallback


class _ResponsiveTableHandle:
    def __init__(self, container: Any, desktop_table: ui.table, mobile_table: ui.table) -> None:
        self.container = container
        self.desktop_table = desktop_table
        self.mobile_table = mobile_table

    def add_slot(self, name: str, template: str) -> "_ResponsiveTableHandle":
        self.desktop_table.add_slot(name, template)
        return self

    def on(self, event: str, handler: Callable[..., Any]) -> "_ResponsiveTableHandle":
        self.desktop_table.on(event, handler)
        self.mobile_table.on(event, handler)
        return self

    def classes(self, value: str | None = None, **kwargs: Any) -> "_ResponsiveTableHandle":
        if value is not None:
            self.container.classes(value)
            self.desktop_table.classes(value)
            self.mobile_table.classes(value)
        if kwargs:
            self.container.classes(**kwargs)
            self.desktop_table.classes(**kwargs)
            self.mobile_table.classes(**kwargs)
        return self


def _erp_table(
    *,
    columns: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    row_key: str = "id",
    rows_per_page: int = 25,
) -> ui.table | _ResponsiveTableHandle:
    table = ui.table(
        columns=columns,
        rows=rows,
        row_key=row_key,
        pagination={"rowsPerPage": rows_per_page},
    ).classes("w-full bm-card bm-table")
    table.props("flat dense wrap-cells separator=horizontal")
    return table


def _responsive_erp_table(
    *,
    columns: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    row_key: str = "id",
    rows_per_page: int = 25,
    mobile_actions_slot: str | None = None,
) -> _ResponsiveTableHandle:
    with ui.column().classes("w-full gap-0 bm-responsive-table") as table_container:
        desktop_table = ui.table(
            columns=columns,
            rows=rows,
            row_key=row_key,
            pagination={"rowsPerPage": rows_per_page},
        ).classes("w-full bm-card bm-table bm-table--desktop")
        desktop_table.props("flat dense wrap-cells separator=horizontal")

        mobile_table = ui.table(
            columns=[{"name": "mobile", "label": "", "field": "mobile"}],
            rows=rows,
            row_key=row_key,
            pagination={"rowsPerPage": rows_per_page},
        ).classes("w-full bm-card bm-table bm-table--mobile")
        mobile_table.props("flat dense hide-header separator=none")
        mobile_table.add_slot(
            "body",
            f"""
            <q-tr :props="props" class="bm-mobile-table-row" @click="$parent.$emit('rowClick', props.row)">
              <q-td key="mobile" :props="props" class="bm-mobile-table-cell">
                <div class="bm-mobile-table-card">
                  <div class="bm-mobile-table-head">
                    <div class="bm-mobile-table-main">
                      <div class="bm-mobile-table-title">{{{{ props.row.mobile_title || '-' }}}}</div>
                      <div v-if="props.row.mobile_title_note" class="bm-mobile-table-title-note">
                        {{{{ props.row.mobile_title_note }}}}
                      </div>
                    </div>
                    <div v-if="props.row.mobile_badge || {str(bool(mobile_actions_slot)).lower()}" class="bm-mobile-table-side" @click.stop>
                      <q-badge
                        v-if="props.row.mobile_badge"
                        :color="props.row.mobile_badge_color || 'grey-6'"
                      >
                        {{{{ props.row.mobile_badge }}}}
                      </q-badge>
                      <q-tooltip v-if="props.row.mobile_badge_tooltip">
                        {{{{ props.row.mobile_badge_tooltip }}}}
                      </q-tooltip>
                      {mobile_actions_slot or ""}
                    </div>
                  </div>
                  <div v-if="props.row.mobile_primary_left || props.row.mobile_primary_right" class="bm-mobile-table-detail-row">
                    <span v-if="props.row.mobile_primary_left" class="bm-mobile-table-detail">
                      {{{{ props.row.mobile_primary_left }}}}
                    </span>
                    <span v-if="props.row.mobile_primary_right" class="bm-mobile-table-detail bm-mobile-table-detail--right">
                      {{{{ props.row.mobile_primary_right }}}}
                    </span>
                  </div>
                  <div v-if="props.row.mobile_secondary" class="bm-mobile-table-detail-row">
                    <span class="bm-mobile-table-detail bm-mobile-table-detail--muted">
                      {{{{ props.row.mobile_secondary }}}}
                    </span>
                  </div>
                </div>
              </q-td>
            </q-tr>
            """,
        )

    return _ResponsiveTableHandle(container=table_container, desktop_table=desktop_table, mobile_table=mobile_table)


def _icon_option_html(label: str, icon: str) -> str:
    return (
        "<span style='display:inline-flex;align-items:center;gap:8px;'>"
        f"<span class='material-icons' style='font-size:18px;line-height:1'>{icon}</span>"
        f"<span>{label}</span>"
        "</span>"
    )


@contextmanager
def _shell(
    active_path: str,
    title: str,
    *,
    show_page_head: bool = True,
    navigate_to: Callable[[str], Any] | None = None,
    rerender_path: str | None = None,
):
    def navigate(path: str, *, close_mobile: bool = True) -> Any:
        if close_mobile:
            _NAV_STATE["sidebar_mobile_open"] = False
        if navigate_to is not None:
            return navigate_to(path)
        return ui.navigate.to(path)

    def rerender_current_shell() -> None:
        ui.navigate.to(rerender_path or active_path)

    def context_class(path: str) -> str:
        if path.startswith("/belege") or path.startswith("/lieferanten") or path.startswith("/kategorien") or path.startswith(
            "/import"
        ):
            return "bm-context-expenses"
        if path.startswith("/verkaeufe"):
            return "bm-context-reports"
        if path.startswith("/projekte"):
            return "bm-context-works"
        if path.startswith("/auswertung"):
            return "bm-context-reports"
        if path.startswith("/einstellungen"):
            return "bm-context-settings"
        return "bm-context-dashboard"

    is_expanded = bool(_NAV_STATE["sidebar_expanded"])
    is_mobile_sidebar_open = bool(_NAV_STATE["sidebar_mobile_open"])
    is_effectively_expanded = is_expanded or is_mobile_sidebar_open

    def group_active(group: dict[str, Any]) -> bool:
        return any(active_path == item.get("path") for item in group.get("items", []))

    open_groups = dict(_NAV_STATE.get("open_groups") or {})
    previous_path = _NAV_STATE.get("last_path")
    if previous_path != active_path:
        for entry in _NAV_CONFIG:
            if entry.get("type") != "group":
                continue
            key = str(entry.get("key") or "")
            if not key:
                continue
            open_groups[key] = group_active(entry)
    _NAV_STATE["open_groups"] = open_groups
    _NAV_STATE["last_path"] = active_path
    flash_notification = _consume_flash_notification()

    def toggle_sidebar() -> None:
        _NAV_STATE["sidebar_expanded"] = not bool(_NAV_STATE["sidebar_expanded"])
        rerender_current_shell()

    def close_mobile_sidebar() -> None:
        if not bool(_NAV_STATE["sidebar_mobile_open"]):
            return
        _NAV_STATE["sidebar_mobile_open"] = False
        rerender_current_shell()

    def toggle_mobile_sidebar() -> None:
        _NAV_STATE["sidebar_mobile_open"] = not bool(_NAV_STATE["sidebar_mobile_open"])
        rerender_current_shell()

    def toggle_group(group_key: str) -> None:
        next_open_groups = dict(_NAV_STATE.get("open_groups") or {})
        group_entry = next(
            (
                entry
                for entry in _NAV_CONFIG
                if entry.get("type") == "group" and str(entry.get("key") or "") == group_key
            ),
            None,
        )
        if not group_entry:
            return
        is_open = bool(next_open_groups.get(group_key))
        if not is_open:
            next_open_groups[group_key] = True
            _NAV_STATE["open_groups"] = next_open_groups
            rerender_current_shell()
            return
        next_open_groups[group_key] = False
        _NAV_STATE["open_groups"] = next_open_groups
        rerender_current_shell()

    def nav_item(path: str, label: str, icon: str, *, nested: bool = False) -> None:
        active = active_path == path
        button_label = label if is_effectively_expanded else ""
        classes = "bm-nav-item w-full"
        if active:
            classes += " bm-nav-item--active"
        if nested and is_effectively_expanded:
            classes += " bm-nav-item--nested"
        if nested and not is_effectively_expanded:
            classes += " bm-nav-item--nested-mini"
        if not is_effectively_expanded:
            classes += " bm-nav-item--mini"
        button_props = "flat no-caps align=left"
        if not is_effectively_expanded:
            button_props = "flat no-caps"
        ui.button(
            button_label,
            icon=icon,
            on_click=lambda p=path: navigate(p),
        ).props(button_props).classes(classes)

    def help_content_for_path(path: str) -> dict[str, str]:
        if path in _HELP_CONTENT_BY_PATH:
            return _HELP_CONTENT_BY_PATH[path]
        for key, value in _HELP_CONTENT_BY_PATH.items():
            if key != "/" and path.startswith(f"{key}/"):
                return value
        return _DEFAULT_HELP_CONTENT

    help_content = help_content_for_path(active_path)
    help_title = help_content.get("title", _DEFAULT_HELP_CONTENT["title"])
    help_body = help_content.get("body", _DEFAULT_HELP_CONTENT["body"])

    with ui.column().classes(f"bm-app-root w-full {context_class(active_path)}"):
        if flash_notification:
            ui.timer(
                0.05,
                lambda payload=flash_notification: ui.notify(payload["message"], type=payload["type"]),
                once=True,
            )
        with ui.row().classes("bm-global-header w-full items-center"):
            with ui.row().classes("bm-global-header-inner w-full items-center justify-between"):
                with ui.row().classes("items-center gap-2"):
                    ui.button(
                        icon="menu" if not is_mobile_sidebar_open else "close",
                        on_click=toggle_mobile_sidebar,
                    ).props("flat round dense").classes("bm-global-icon-btn bm-mobile-nav-btn")
                    with ui.element("div").classes("bm-global-brand-badge"):
                        ui.image("/assets/hamster-logo.png").classes("bm-global-brand-logo")
                    ui.label("Atelier Buddy").classes("bm-global-brand")
                with ui.row().classes("items-center gap-1"):
                    with ui.button(icon="help_outline").props("flat round dense").classes("bm-global-icon-btn"):
                        with ui.menu().props(
                            'anchor="bottom right" self="top right" :offset="[0, 16]" auto-close'
                        ).classes("bm-help-menu") as help_menu:
                            with ui.column().classes("bm-help-panel"):
                                with ui.row().classes("w-full items-start justify-between gap-3"):
                                    ui.label(help_title).classes("bm-help-popover-title")
                                    ui.button(icon="close", on_click=help_menu.close).props(
                                        "flat round dense size=sm"
                                    ).classes("bm-help-popover-close")
                                with ui.column().classes("bm-help-popover-body w-full"):
                                    ui.label(help_body).classes("text-sm")
                    ui.button(
                        icon="settings",
                        on_click=lambda: navigate("/einstellungen"),
                    ).props("flat round dense").classes("bm-global-icon-btn")
                    ui.button(
                        icon="logout",
                        on_click=lambda: navigate("/logout"),
                    ).props("flat round dense").classes("bm-global-icon-btn")

        backdrop_classes = "bm-sidebar-backdrop"
        if is_mobile_sidebar_open:
            backdrop_classes += " bm-sidebar-backdrop--open"
        ui.element("div").classes(backdrop_classes).on("click", lambda _: close_mobile_sidebar())

        with ui.row().classes("bm-app-shell w-full"):
            sidebar_classes = "bm-sidebar"
            if not is_effectively_expanded:
                sidebar_classes += " bm-sidebar--mini"
            if is_mobile_sidebar_open:
                sidebar_classes += " bm-sidebar--mobile-open"

            with ui.column().classes(sidebar_classes):
                with ui.row().classes("bm-sidebar-header w-full items-center justify-center"):
                    ui.button(
                        "",
                        icon="keyboard_double_arrow_left" if is_expanded else "keyboard_double_arrow_right",
                        on_click=toggle_sidebar,
                    ).props("flat no-caps").classes("bm-nav-item bm-nav-item--mini bm-sidebar-toggle-btn w-full")

                for entry in _NAV_CONFIG:
                    entry_type = entry.get("type")
                    if entry_type == "item":
                        nav_item(str(entry["path"]), str(entry["label"]), str(entry["icon"]))
                        continue
                    if entry_type != "group":
                        continue

                    group_key = str(entry.get("key") or "")
                    if not group_key:
                        continue
                    is_group_open = bool((_NAV_STATE.get("open_groups") or {}).get(group_key))
                    is_group_active = group_active(entry)
                    if is_effectively_expanded:
                        group_classes = "bm-nav-item bm-nav-group-trigger w-full"
                        if is_group_active:
                            group_classes += " bm-nav-item--active"
                        if is_group_open:
                            group_classes += " bm-nav-group-trigger--open"
                        ui.button(
                            str(entry.get("label") or ""),
                            icon=str(entry.get("icon") or "folder"),
                            on_click=lambda k=group_key: toggle_group(k),
                        ).props("flat no-caps align=left").classes(group_classes)
                    else:
                        group_classes = "bm-nav-item bm-nav-item--mini bm-nav-group-trigger w-full"
                        if is_group_active:
                            group_classes += " bm-nav-item--active"
                        if is_group_open:
                            group_classes += " bm-nav-group-trigger--open"
                        ui.button(
                            "",
                            icon=str(entry.get("icon") or "folder"),
                            on_click=lambda k=group_key: toggle_group(k),
                        ).props("flat no-caps").classes(group_classes)
                    if is_group_open:
                        for item in entry.get("items", []):
                            nav_item(str(item.get("path")), str(item.get("label")), str(item.get("icon")), nested=True)

            with ui.column().classes("bm-content"):
                if show_page_head:
                    with ui.row().classes("bm-page-head w-full items-center justify-between"):
                        with ui.column().classes("gap-1"):
                            ui.label(title).classes("bm-page-title")
                with ui.column().classes("w-full max-w-7xl mx-auto gap-4"):
                    yield


def register_pages(services: ServiceContainer) -> None:
    masterdata = services.masterdata_service

    def project_options(active_only: bool = True, include_ids: list[int] | None = None) -> dict[int, str]:
        include_set = {item for item in (include_ids or []) if isinstance(item, int)}
        with Session(engine) as session:
            stmt = select(Project).order_by(Project.name)
            if active_only:
                if include_set:
                    stmt = stmt.where(or_(Project.active.is_(True), Project.id.in_(include_set)))
                else:
                    stmt = stmt.where(Project.active.is_(True))
            projects = list(session.exec(stmt).all())
        return {
            project.id: (f"{project.name} (inaktiv)" if not project.active else project.name)
            for project in projects
            if project.id is not None
        }

    def cost_type_options(active_only: bool = True, include_ids: list[int] | None = None) -> dict[int, str]:
        include_set = {item for item in (include_ids or []) if isinstance(item, int)}
        with Session(engine) as session:
            stmt = select(CostType).order_by(CostType.name)
            if active_only:
                if include_set:
                    stmt = stmt.where(or_(CostType.active.is_(True), CostType.id.in_(include_set)))
                else:
                    stmt = stmt.where(CostType.active.is_(True))
            cost_types = list(session.exec(stmt).all())
        return {item.id: item.name for item in cost_types if item.id is not None}

    def cost_subcategory_options(
        active_only: bool = True,
        cost_type_ids: list[int] | None = None,
        include_ids: list[int] | None = None,
    ) -> dict[int, str]:
        include_set = {item for item in (include_ids or []) if isinstance(item, int)}
        with Session(engine) as session:
            stmt = select(CostSubcategory)
            if active_only:
                if include_set:
                    stmt = stmt.where(or_(CostSubcategory.active.is_(True), CostSubcategory.id.in_(include_set)))
                else:
                    stmt = stmt.where(CostSubcategory.active.is_(True))
            if cost_type_ids:
                stmt = stmt.where(CostSubcategory.cost_type_id.in_(cost_type_ids))
            subcategories = list(session.exec(stmt).all())
        subcategories.sort(
            key=lambda item: (
                item.cost_type_id,
                0 if item.is_system_default else 1,
                (item.name or "").casefold(),
            )
        )
        return {item.id: item.name for item in subcategories if item.id is not None}

    def supplier_options(active_only: bool = True) -> dict[int, str]:
        with Session(engine) as session:
            stmt = select(Supplier).order_by(Supplier.name)
            if active_only:
                stmt = stmt.where(Supplier.active.is_(True))
            suppliers = list(session.exec(stmt).all())
        return {supplier.id: supplier.name for supplier in suppliers if supplier.id is not None}

    def contact_options() -> dict[int, str]:
        with Session(engine) as session:
            contacts = list(session.exec(select(Contact).order_by(Contact.family_name, Contact.given_name)).all())
        return {
            contact.id: _contact_display_name(contact)
            for contact in contacts
            if contact.id is not None
        }

    def contact_category_options() -> dict[int, str]:
        with Session(engine) as session:
            stmt = select(ContactCategory).order_by(ContactCategory.name)
            categories = list(session.exec(stmt).all())
        return {item.id: item.name for item in categories if item.id is not None}

    def country_options() -> dict[str, str]:
        return dict(COUNTRY_OPTION_MAP)

    def build_contact_inputs(
        *,
        current_contact: Contact | None = None,
        category_options_map: dict[int, str] | None = None,
        selected_category_id: int | None = None,
        include_category: bool = True,
        include_extended_fields: bool = True,
        include_notes: bool = True,
    ) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        selected_country = (
            (current_contact.country or "").strip().upper()
            if current_contact and (current_contact.country or "").strip()
            else DEFAULT_CONTACT_COUNTRY_CODE
        )

        with ui.row().classes("w-full gap-3 wrap bm-form-row"):
            fields["given_name"] = ui.input(
                "Vorname",
                value="" if current_contact is None else (current_contact.given_name or ""),
            ).classes("flex-1 min-w-[220px] bm-form-field")
            fields["family_name"] = ui.input(
                "Nachname",
                value="" if current_contact is None else (current_contact.family_name or ""),
            ).classes("flex-1 min-w-[220px] bm-form-field")

        with ui.row().classes("w-full gap-3 wrap bm-form-row"):
            fields["organisation"] = ui.input(
                "Organisation",
                value="" if current_contact is None else (current_contact.organisation or ""),
            ).classes("flex-1 min-w-[220px] bm-form-field")
            if include_category:
                fields["contact_category_id"] = ui.select(
                    category_options_map or {},
                    value=selected_category_id,
                    label="Kategorie",
                ).classes("flex-1 min-w-[220px] bm-form-field")
            else:
                fields["email"] = ui.input(
                    "E-Mail",
                    value="" if current_contact is None else (current_contact.email or ""),
                ).classes("flex-1 min-w-[220px] bm-form-field")

        if include_extended_fields:
            with ui.card().classes("bm-card p-4 w-full gap-3"):
                ui.label("Adresse").classes("text-base font-semibold")
                with ui.row().classes("w-full gap-3 wrap bm-form-row"):
                    fields["street"] = ui.input(
                        "Straße",
                        value="" if current_contact is None else (current_contact.street or ""),
                    ).classes("flex-1 min-w-[240px] bm-form-field")
                    fields["house_number"] = ui.input(
                        "Hausnummer",
                        value="" if current_contact is None else (current_contact.house_number or ""),
                    ).classes("w-36 bm-form-field")
                with ui.row().classes("w-full gap-3 wrap bm-form-row"):
                    fields["address_extra"] = ui.input(
                        "Adresszusatz",
                        value="" if current_contact is None else (current_contact.address_extra or ""),
                    ).classes("w-full bm-form-field")
                with ui.row().classes("w-full gap-3 wrap bm-form-row"):
                    fields["postal_code"] = ui.input(
                        "PLZ",
                        value="" if current_contact is None else (current_contact.postal_code or ""),
                    ).classes("w-40 bm-form-field")
                    fields["city"] = ui.input(
                        "Ort",
                        value="" if current_contact is None else (current_contact.city or ""),
                    ).classes("flex-1 min-w-[220px] bm-form-field")
                fields["country"] = ui.select(
                    country_options(),
                    value=selected_country,
                    label="Land",
                ).props("use-input input-debounce=0").classes("w-full bm-form-field")
        else:
            fields["street"] = None
            fields["house_number"] = None
            fields["address_extra"] = None
            fields["postal_code"] = None
            fields["city"] = None
            fields["country"] = None

        if include_category:
            with ui.card().classes("bm-card p-4 w-full gap-3"):
                ui.label("Kontakt").classes("text-base font-semibold")
                with ui.row().classes("w-full gap-3 wrap bm-form-row"):
                    fields["email"] = ui.input(
                        "E-Mail",
                        value="" if current_contact is None else (current_contact.email or ""),
                    ).classes("flex-1 min-w-[220px] bm-form-field")
                    fields["phone"] = ui.input(
                        "Telefon",
                        value="" if current_contact is None else (current_contact.phone or ""),
                    ).classes("flex-1 min-w-[220px] bm-form-field")
                with ui.row().classes("w-full gap-3 wrap bm-form-row"):
                    fields["mobile"] = ui.input(
                        "Mobil",
                        value="" if current_contact is None else (current_contact.mobile or ""),
                    ).classes("flex-1 min-w-[220px] bm-form-field")
                    fields["primary_link"] = ui.input(
                        "Link",
                        value="" if current_contact is None else (current_contact.primary_link or ""),
                    ).classes("flex-1 min-w-[220px] bm-form-field")
        else:
            fields["phone"] = None
            fields["mobile"] = None
            fields["primary_link"] = None

        if include_notes:
            fields["notes"] = ui.textarea(
                "Notiz",
                value="" if current_contact is None else (current_contact.notes or ""),
            ).classes("w-full bm-order-notes bm-form-field")
        else:
            fields["notes"] = None

        return fields

    def contact_form_values(fields: dict[str, Any]) -> dict[str, Any]:
        def value_of(name: str) -> Any:
            field = fields.get(name)
            return None if field is None else field.value

        return {
            "given_name": value_of("given_name"),
            "family_name": value_of("family_name"),
            "organisation": value_of("organisation"),
            "email": value_of("email"),
            "phone": value_of("phone"),
            "mobile": value_of("mobile"),
            "primary_link": value_of("primary_link"),
            "street": value_of("street"),
            "house_number": value_of("house_number"),
            "address_extra": value_of("address_extra"),
            "postal_code": value_of("postal_code"),
            "city": value_of("city"),
            "country": value_of("country") or DEFAULT_CONTACT_COUNTRY_CODE,
            "notes": value_of("notes"),
        }

    def missing_required_fields(receipt: Receipt) -> list[str]:
        return ReceiptCompletionService().evaluate_receipt(receipt).missing_fields

    def open_in_new_tab(url: str) -> None:
        _run_client_javascript(context.client, f"window.open({json.dumps(url)}, '_blank')")

    def create_dirty_guard(
        flag_name: str,
    ) -> tuple[Callable[[], None], Callable[[], None], Callable[[], bool], Callable[[str], Any], Callable[[str], Any]]:
        client = context.client
        dirty_state = {"dirty": False}
        _run_client_javascript(
            client,
            f"""
            (() => {{
              const flagName = {json.dumps(flag_name)};
              window[flagName] = false;
              window.__atelierBuddyDirtyGuards = window.__atelierBuddyDirtyGuards || {{}};
              if (!window.__atelierBuddyDirtyGuards[flagName]) {{
                const handler = (event) => {{
                  if (!window[flagName]) return;
                  event.preventDefault();
                  event.returnValue = '';
                  return '';
                }};
                window.addEventListener('beforeunload', handler);
                window.__atelierBuddyDirtyGuards[flagName] = handler;
              }}
            }})();
            """
        )

        def mark_dirty() -> None:
            if dirty_state["dirty"]:
                return
            dirty_state["dirty"] = True
            _run_client_javascript(client, f"window[{json.dumps(flag_name)}] = true;")

        def mark_clean() -> None:
            dirty_state["dirty"] = False
            _run_client_javascript(client, f"window[{json.dumps(flag_name)}] = false;")

        def is_dirty() -> bool:
            return dirty_state["dirty"]

        async def clean_and_navigate(path: str) -> None:
            dirty_state["dirty"] = False
            await _await_client_javascript(client, f"window[{json.dumps(flag_name)}] = false;", timeout=30)
            target_path = str(path or "/")
            try:
                ui.navigate.to(target_path)
            except Exception:
                LOG.debug("Serverseitige Navigation fehlgeschlagen, weiche auf Client-Navigation aus", exc_info=True)
            await _await_client_javascript(
                client,
                f"window.location.assign({json.dumps(target_path)}); return true;",
                timeout=1.0,
            )

        async def guarded_navigate(path: str) -> None:
            if dirty_state["dirty"]:
                should_leave = await _await_client_javascript(
                    client,
                    "return window.confirm('Es gibt ungespeicherte Änderungen. Seite wirklich verlassen?');",
                    timeout=30,
                )
                if not should_leave:
                    return
            await clean_and_navigate(path)

        return mark_dirty, mark_clean, is_dirty, guarded_navigate, clean_and_navigate

    def open_import_dialog(on_import_done: Callable[[], None] | None = None) -> None:
        with ui.dialog() as dialog, ui.card().classes("bm-card p-5 w-[820px] max-w-full"):
            ui.label("Belege importieren").classes("text-2xl font-semibold")
            ui.label("Dateien oder einen Ordner auswählen und danach Import starten.").classes("text-sm text-slate-600")

            staged_uploads: list[dict[str, Any]] = []
            staging_summary = ui.label("Noch keine Dateien ausgewählt.").classes("text-sm text-slate-600")
            staging_column = ui.column().classes("w-full gap-2")

            def render_staging() -> None:
                staging_column.clear()
                with staging_column:
                    if not staged_uploads:
                        ui.label("Keine Dateien im Import.")
                        return

                    for item in staged_uploads:
                        with ui.row().classes("w-full items-center justify-between bm-card p-2"):
                            with ui.row().classes("items-center gap-2"):
                                ui.icon("description")
                                ui.label(item["name"]).classes("font-medium")
                                ui.label(_human_size(item["size"])).classes("text-xs text-slate-500")
                            ui.button(
                                icon="delete_outline",
                                on_click=lambda sid=item["id"]: remove_staged_item(sid),
                            ).props("flat round dense color=negative")

            def remove_staged_item(staged_id: str) -> None:
                staged_uploads[:] = [item for item in staged_uploads if item["id"] != staged_id]
                staging_summary.text = f"{len(staged_uploads)} Datei(en) ausgewählt."
                render_staging()

            async def handle_upload(event: events.MultiUploadEventArguments) -> None:
                added = 0
                skipped = 0
                for file_upload in event.files:
                    if not is_supported_filename(file_upload.name):
                        skipped += 1
                        continue

                    try:
                        size = int(file_upload.size())
                    except Exception:
                        size = 0

                    staged_uploads.append(
                        {
                            "id": uuid.uuid4().hex,
                            "file": file_upload,
                            "name": file_upload.name,
                            "size": size,
                        }
                    )
                    added += 1

                if added:
                    ui.notify(f"{added} Datei(en) hinzugefügt", type="positive")
                if skipped:
                    ui.notify(f"{skipped} Datei(en) wegen Dateityp ignoriert", type="warning")

                staging_summary.text = f"{len(staged_uploads)} Datei(en) ausgewählt."
                render_staging()

            upload_widget = ui.upload(
                multiple=True,
                auto_upload=True,
                on_multi_upload=handle_upload,
                label="Dateien hierher ziehen oder auswählen",
            ).classes("w-full bm-upload-zone")
            upload_widget.props("accept=.pdf,.jpg,.jpeg,.png,.heic,.heif")

            async def start_import() -> None:
                if not staged_uploads:
                    ui.notify("Bitte zuerst Dateien auswählen", type="warning")
                    return

                files = [entry["file"] for entry in staged_uploads]
                try:
                    batch = await services.import_service.import_uploaded_files(files)
                    ui.notify(
                        f"Import gestartet: {batch.imported_count}/{batch.total_count} Datei(en) angelegt",
                        type="positive",
                    )
                    dialog.close()
                    if on_import_done:
                        on_import_done()
                except Exception as exc:
                    _notify_error("Import fehlgeschlagen", exc)

            with ui.row().classes("w-full justify-between items-center"):
                ui.button("Abbrechen", on_click=dialog.close).props("flat")
                with ui.row().classes("gap-2"):
                    ui.button(
                        "Auswahl leeren",
                        on_click=lambda: clear_staging(),
                    ).props("flat")
                    ui.button("Import starten", icon="upload_file", on_click=start_import).props("color=primary")

            def clear_staging() -> None:
                staged_uploads.clear()
                upload_widget.reset()
                staging_summary.text = "Noch keine Dateien ausgewählt."
                render_staging()

            render_staging()
        dialog.open()

    @ui.page("/")
    def dashboard_page() -> None:
        with _shell("/", "Dashboard"):
            stats_config = [
                ("belege", "Belege", "/belege"),
                ("projekte", "Projekte", "/projekte"),
                ("anbieter", "Anbieter", "/lieferanten"),
                ("kategorien", "Kostenkategorien", "/kategorien"),
            ]
            stat_values: dict[str, ui.label] = {}
            taskboard_column = ui.column().classes("w-full gap-3")

            ui.label("Übersicht").classes("text-lg font-semibold bm-on-dark-title")
            with ui.row().classes("w-full gap-4 wrap"):
                for key, label, target_path in stats_config:
                    stat_card = ui.card().classes("bm-card bm-stat-card p-4 w-44 cursor-pointer")
                    stat_card.on("click", lambda _, nav=target_path: ui.navigate.to(nav))
                    with stat_card:
                        ui.label(label).classes("text-sm bm-stat-label")
                        stat_values[key] = ui.label("0").classes("text-3xl font-bold")

            def open_receipt_detail(receipt_id: int | None) -> None:
                if not receipt_id:
                    return
                ui.navigate.to(f"/belege/{receipt_id}")

            def format_upload_date(uploaded_at: datetime | None) -> str:
                if uploaded_at is None:
                    return "-"
                dt = uploaded_at if uploaded_at.tzinfo else uploaded_at.replace(tzinfo=timezone.utc)
                return dt.astimezone().strftime("%d.%m.%Y")

            def render_taskboard(receipts: list[Receipt], remaining_count: int) -> None:
                taskboard_column.clear()
                with taskboard_column:
                    ui.button(
                        "Belege hochladen",
                        icon="upload_file",
                        on_click=lambda: open_import_dialog(refresh_dashboard),
                    ).props("color=primary unelevated no-caps").classes("bm-toolbar-btn")
                    ui.label("Belege mit fehlenden Angaben").classes("text-lg font-semibold bm-on-dark-title")

                    if not receipts:
                        with ui.card().classes("bm-card bm-status-positive-card p-4"):
                            ui.label("Alles erledigt. Sehr gut!").classes("text-base font-semibold bm-status-positive-text")
                        return

                    with ui.row().classes("w-full gap-3 items-start wrap"):
                        for receipt in receipts:
                            thumb_url = to_files_url(receipt.thumbnail_path)
                            thumb_card = ui.card().classes("bm-card bm-dashboard-thumb-card cursor-pointer")
                            thumb_card.on("click", lambda _, rid=receipt.id: open_receipt_detail(rid))
                            with thumb_card:
                                with ui.element("div").classes("bm-dashboard-thumb-media"):
                                    if thumb_url:
                                        ui.image(thumb_url).classes("w-full h-full rounded-lg object-cover")
                                    else:
                                        with ui.element("div").classes(
                                            "w-full h-full rounded-lg bg-slate-100 flex items-center justify-center"
                                        ):
                                            ui.icon("description", size="36px")
                                with ui.element("div").classes("bm-dashboard-thumb-meta"):
                                    ui.label("Hochgeladen am:").classes("bm-dashboard-thumb-caption")
                                    ui.label(format_upload_date(receipt.created_at)).classes("bm-dashboard-thumb-date")

                    if remaining_count > 0:
                        ui.label(f"+{remaining_count} weitere offen").classes("text-sm bm-on-dark-title")

            def refresh_dashboard() -> None:
                with Session(engine) as session:
                    total_receipts = len(session.exec(select(Receipt.id).where(Receipt.deleted_at.is_(None))).all())
                    total_projects = len(session.exec(select(Project.id)).all())
                    total_categories = len(session.exec(select(CostType.id)).all())
                    total_suppliers = len(session.exec(select(Supplier.id)).all())

                    receipts = list(
                        session.exec(
                            select(Receipt)
                            .where(Receipt.deleted_at.is_(None))
                            .options(selectinload(Receipt.allocations).selectinload(CostAllocation.cost_subcategory))
                            .order_by(Receipt.updated_at.desc(), Receipt.created_at.desc())
                        ).all()
                    )
                    receipts_with_missing = [receipt for receipt in receipts if missing_required_fields(receipt)]
                    shown_receipts = receipts_with_missing[:12]
                    remaining_count = max(0, len(receipts_with_missing) - len(shown_receipts))

                stat_values["belege"].text = str(total_receipts)
                stat_values["projekte"].text = str(total_projects)
                stat_values["kategorien"].text = str(total_categories)
                stat_values["anbieter"].text = str(total_suppliers)
                render_taskboard(shown_receipts, remaining_count)

            refresh_dashboard()
            ui.timer(5.0, refresh_dashboard)

    @ui.page("/import")
    def import_page() -> None:
        ui.navigate.to("/belege")

    @ui.page("/belege")
    def receipts_page() -> None:
        with _shell("/belege", "Belege"):
            with ui.card().classes("bm-card p-4 w-full"):
                view_mode = "active"
                filters_visible = False
                search_task: asyncio.Task | None = None
                is_refreshing_filters = False

                with ui.row().classes("w-full items-center justify-between gap-3 wrap"):
                    with ui.row().classes("gap-2 wrap"):
                        active_view_button = ui.button("Belege").props("unelevated no-caps").classes(
                            "bm-view-mode-btn bm-segment-btn"
                        )
                        deleted_view_button = ui.button("Gelöschte Belege").props("unelevated no-caps").classes(
                            "bm-view-mode-btn bm-segment-btn"
                        )
                    with ui.row().classes("gap-2 wrap"):
                        filter_toggle_button = ui.button(
                            "Filter",
                            icon="filter_alt",
                        ).props("flat no-caps").classes("bm-filter-btn")
                        ui.button(
                            "Importieren",
                            icon="upload_file",
                            on_click=lambda: open_import_dialog(render_results),
                        ).props("color=primary unelevated no-caps").classes("bm-filter-btn bm-toolbar-btn")

                filter_row = ui.row().classes("w-full bm-filter-row hidden")
                with filter_row:
                    query_input = ui.input("Volltextsuche").classes("min-w-72 bm-filter-field")
                    query_input.props("clearable")
                    work_select = ui.select(
                        {},
                        label="Projekte",
                        multiple=True,
                        with_input=True,
                        clearable=True,
                    ).classes("min-w-56 w-56 bm-filter-field")
                    work_select.props("use-chips")
                    supplier_select = ui.select(
                        {},
                        label="Anbieter",
                        multiple=True,
                        with_input=True,
                        clearable=True,
                    ).classes("min-w-56 w-56 bm-filter-field")
                    supplier_select.props("use-chips")
                    cost_type_select = ui.select(
                        {},
                        label="Kostenkategorien",
                        multiple=True,
                        with_input=True,
                        clearable=True,
                    ).classes("min-w-56 w-56 bm-filter-field")
                    cost_type_select.props("use-chips")
                    cost_subcategory_select = ui.select(
                        {},
                        label="Unterkategorien",
                        multiple=True,
                        with_input=True,
                        clearable=True,
                    ).classes("min-w-56 w-56 bm-filter-field")
                    cost_subcategory_select.props("use-chips")
                    date_from_input = ui.input("Datum von").props("type=date clearable").classes("w-40 bm-filter-field")
                    date_to_input = ui.input("Datum bis").props("type=date clearable").classes("w-40 bm-filter-field")
                    ui.button("Filter löschen", icon="close", on_click=lambda: clear_filters()).props(
                        "flat"
                    ).classes("bm-filter-btn")

                results_column = ui.column().classes("w-full gap-3")

                def open_detail_page(receipt_id: int) -> None:
                    if receipt_id <= 0:
                        return
                    ui.navigate.to(f"/belege/{receipt_id}")

                def apply_view_button_styles() -> None:
                    active_view_button.classes(remove="bm-segment-btn--active bm-segment-btn--inactive")
                    deleted_view_button.classes(remove="bm-segment-btn--active bm-segment-btn--inactive")
                    if view_mode == "active":
                        active_view_button.classes(add="bm-segment-btn--active")
                        deleted_view_button.classes(add="bm-segment-btn--inactive")
                    else:
                        active_view_button.classes(add="bm-segment-btn--inactive")
                        deleted_view_button.classes(add="bm-segment-btn--active")

                def set_view_mode(next_mode: str) -> None:
                    nonlocal view_mode
                    if next_mode == view_mode:
                        return
                    view_mode = next_mode
                    apply_view_button_styles()
                    render_results()

                def schedule_render(delay_seconds: float = 0.35) -> None:
                    nonlocal search_task
                    if search_task and not search_task.done():
                        search_task.cancel()

                    async def delayed() -> None:
                        try:
                            await asyncio.sleep(delay_seconds)
                        except asyncio.CancelledError:
                            return
                        render_results()

                    search_task = asyncio.create_task(delayed())

                def clear_filters() -> None:
                    query_input.value = ""
                    work_select.value = []
                    supplier_select.value = []
                    cost_type_select.value = []
                    cost_subcategory_select.value = []
                    date_from_input.value = ""
                    date_to_input.value = ""
                    render_results()

                def apply_filter_visibility() -> None:
                    filter_row.classes(remove="hidden")
                    filter_toggle_button.classes(remove="bm-segment-btn--active bm-segment-btn--inactive")
                    if filters_visible:
                        filter_toggle_button.classes(add="bm-segment-btn bm-segment-btn--active")
                    else:
                        filter_row.classes(add="hidden")
                        filter_toggle_button.classes(add="bm-segment-btn bm-segment-btn--inactive")

                def toggle_filters() -> None:
                    nonlocal filters_visible
                    filters_visible = not filters_visible
                    apply_filter_visibility()

                def refresh_filter_options() -> None:
                    nonlocal is_refreshing_filters
                    if is_refreshing_filters:
                        return
                    is_refreshing_filters = True
                    try:
                        work_options = project_options(active_only=False)
                        supplier_options_map = supplier_options(active_only=False)
                        current_cost_types = _to_int_list(cost_type_select.value)
                        cost_type_options_map = cost_type_options(active_only=True, include_ids=current_cost_types)
                        current_work = [item for item in _to_int_list(work_select.value) if item in work_options]
                        current_suppliers = [
                            item for item in _to_int_list(supplier_select.value) if item in supplier_options_map
                        ]
                        current_cost_types = [item for item in current_cost_types if item in cost_type_options_map]
                        work_select.set_options(work_options, value=current_work)
                        supplier_select.set_options(supplier_options_map, value=current_suppliers)
                        cost_type_select.set_options(cost_type_options_map, value=current_cost_types)
                        selected_cost_types = _to_int_list(cost_type_select.value)
                        current_subcategories = _to_int_list(cost_subcategory_select.value)
                        cost_subcategory_options_map = cost_subcategory_options(
                            active_only=True,
                            cost_type_ids=selected_cost_types or None,
                            include_ids=current_subcategories,
                        )
                        allowed_subcategories = set(cost_subcategory_options_map.keys())
                        cost_subcategory_select.set_options(
                            cost_subcategory_options_map,
                            value=[item for item in current_subcategories if item in allowed_subcategories],
                        )
                    finally:
                        is_refreshing_filters = False

                def hard_delete_receipt(receipt_id: int, rerender: callable) -> None:
                    with ui.dialog() as dialog, ui.card().classes("p-4 max-w-lg"):
                        ui.label("Beleg endgültig löschen?").classes("text-lg font-semibold")
                        ui.label("Dateien und Datenbankeintrag werden dauerhaft entfernt.")
                        with ui.row().classes("w-full justify-end gap-2"):
                            ui.button("Abbrechen", on_click=dialog.close).props("flat")

                            def execute_delete() -> None:
                                try:
                                    services.receipt_service.hard_delete(receipt_id)
                                    ui.notify("Beleg endgültig gelöscht", type="positive")
                                except Exception as exc:
                                    _notify_error("Endgültiges Löschen fehlgeschlagen", exc)
                                    return
                                dialog.close()
                                rerender()

                            ui.button("Endgültig löschen", on_click=execute_delete).props("color=negative")
                    dialog.open()

                def move_to_deleted(receipt_id: int) -> None:
                    if receipt_id <= 0:
                        return
                    try:
                        services.receipt_service.move_to_trash(receipt_id)
                        ui.notify("Beleg in Gelöschte Belege verschoben", type="positive")
                    except Exception as exc:
                        _notify_error("Löschen fehlgeschlagen", exc)
                        return
                    render_results()

                def restore_receipt(receipt_id: int) -> None:
                    if receipt_id <= 0:
                        return
                    try:
                        services.receipt_service.restore_from_trash(receipt_id)
                        ui.notify("Beleg wiederhergestellt", type="positive")
                    except Exception as exc:
                        _notify_error("Wiederherstellung fehlgeschlagen", exc)
                        return
                    render_results()

                def render_results() -> None:
                    results_column.clear()
                    deleted_view = view_mode == "deleted"

                    receipts = services.search_service.search(
                        query=(query_input.value or "").strip(),
                        project_ids=_to_int_list(work_select.value),
                        cost_type_ids=_to_int_list(cost_type_select.value),
                        cost_subcategory_ids=_to_int_list(cost_subcategory_select.value),
                        supplier_ids=_to_int_list(supplier_select.value),
                        date_from=_parse_iso_date(date_from_input.value),
                        date_to=_parse_iso_date(date_to_input.value),
                        deleted_only=deleted_view,
                    )

                    with results_column:
                        if not receipts:
                            label = (
                                "Keine gelöschten Belege gefunden."
                                if deleted_view
                                else "Keine Belege für die aktuellen Filter gefunden."
                            )
                            with ui.card().classes("bm-card p-4"):
                                ui.label(label)
                            return

                        rows: list[dict[str, Any]] = []
                        for receipt in receipts:
                            missing_fields = missing_required_fields(receipt)
                            completeness = "Vollständig" if not missing_fields else "Pflichtangaben fehlen"
                            rows.append(
                                {
                                    "id": receipt.id,
                                    "supplier": receipt.supplier.name if receipt.supplier else "-",
                                    "doc_date": receipt.doc_date.isoformat() if receipt.doc_date else "-",
                                    "gross": _format_cents(receipt.amount_gross_cents, settings.default_currency),
                                    "completeness": completeness,
                                    "completeness_hint": ", ".join(missing_fields),
                                    "mobile_title": receipt.supplier.name if receipt.supplier else "Unbekannter Anbieter",
                                    "mobile_primary_left": f"Belegdatum {receipt.doc_date.isoformat()}" if receipt.doc_date else "Belegdatum -",
                                    "mobile_primary_right": _format_cents(receipt.amount_gross_cents, settings.default_currency),
                                    "mobile_secondary": (
                                        f"Fehlt: {', '.join(missing_fields)}" if missing_fields else "Pflichtangaben vollständig"
                                    ),
                                    "mobile_badge": completeness,
                                    "mobile_badge_color": "positive" if not missing_fields else "warning",
                                    "mobile_badge_tooltip": (
                                        f"Fehlt: {', '.join(missing_fields)}" if missing_fields else ""
                                    ),
                                    "deleted": bool(receipt.deleted_at),
                                }
                            )

                        columns = [
                            {"name": "supplier", "label": "Anbieter", "field": "supplier", "align": "left"},
                            {"name": "doc_date", "label": "Belegdatum", "field": "doc_date", "align": "left", "sortable": True},
                            {"name": "gross", "label": f"Brutto ({settings.default_currency})", "field": "gross", "align": "right"},
                            {"name": "completeness", "label": "Vollständigkeit", "field": "completeness", "align": "left"},
                            {"name": "actions", "label": "Aktionen", "field": "actions", "align": "right"},
                        ]
                        actions_menu = """
                        <q-btn flat round dense icon="more_vert" @click.stop>
                          <q-menu auto-close>
                            <q-list dense style="min-width: 220px">
                              <q-item clickable @click="$parent.$emit('detail_action', props.row)">
                                <q-item-section avatar><q-icon name="visibility" /></q-item-section>
                                <q-item-section><q-item-label>Details anzeigen</q-item-label></q-item-section>
                              </q-item>
                              <q-item v-if="!props.row.deleted" clickable @click="$parent.$emit('delete_action', props.row)">
                                <q-item-section avatar><q-icon name="delete_outline" color="negative" /></q-item-section>
                                <q-item-section><q-item-label>In Gelöschte Belege</q-item-label></q-item-section>
                              </q-item>
                              <q-item v-if="props.row.deleted" clickable @click="$parent.$emit('restore_action', props.row)">
                                <q-item-section avatar><q-icon name="restore_from_trash" /></q-item-section>
                                <q-item-section><q-item-label>Wiederherstellen</q-item-label></q-item-section>
                              </q-item>
                              <q-item v-if="props.row.deleted" clickable @click="$parent.$emit('hard_delete_action', props.row)">
                                <q-item-section avatar><q-icon name="delete_forever" color="negative" /></q-item-section>
                                <q-item-section><q-item-label>Endgültig löschen</q-item-label></q-item-section>
                              </q-item>
                            </q-list>
                          </q-menu>
                        </q-btn>
                        """
                        table = _responsive_erp_table(
                            columns=columns,
                            rows=rows,
                            row_key="id",
                            rows_per_page=25,
                            mobile_actions_slot=actions_menu,
                        )

                        table.add_slot(
                            "body-cell-completeness",
                            """
                            <q-td :props="props">
                              <q-badge :color="props.row.completeness === 'Vollständig' ? 'positive' : 'warning'">
                                {{ props.row.completeness }}
                              </q-badge>
                              <q-tooltip v-if="props.row.completeness_hint">
                                Fehlt: {{ props.row.completeness_hint }}
                              </q-tooltip>
                            </q-td>
                            """,
                        )
                        table.add_slot(
                            "body-cell-actions",
                            f"""
                            <q-td :props="props" class="text-right">
                              {actions_menu}
                            </q-td>
                            """,
                        )

                        table.on("detail_action", lambda e: open_detail_page(_extract_row_id(e) or -1))
                        table.on("delete_action", lambda e: move_to_deleted(_extract_row_id(e) or -1))
                        table.on("restore_action", lambda e: restore_receipt(_extract_row_id(e) or -1))
                        table.on(
                            "hard_delete_action",
                            lambda e: hard_delete_receipt(_extract_row_id(e) or -1, render_results),
                        )
                        table.on("rowClick", lambda e: open_detail_page(_extract_row_id(e) or -1))

                query_input.on("update:model-value", lambda _: schedule_render(0.35))
                query_input.on("keydown.enter", lambda _: render_results())
                work_select.on_value_change(lambda _: schedule_render(0.05))
                supplier_select.on_value_change(lambda _: schedule_render(0.05))
                cost_type_select.on_value_change(lambda _: (refresh_filter_options(), schedule_render(0.05)))
                cost_subcategory_select.on_value_change(lambda _: schedule_render(0.05))
                date_from_input.on("update:model-value", lambda _: schedule_render(0.05))
                date_to_input.on("update:model-value", lambda _: schedule_render(0.05))

                active_view_button.on("click", lambda _: set_view_mode("active"))
                deleted_view_button.on("click", lambda _: set_view_mode("deleted"))
                filter_toggle_button.on("click", lambda _: toggle_filters())
                apply_view_button_styles()
                apply_filter_visibility()
                refresh_filter_options()
                render_results()

    @ui.page("/belege/{receipt_id}")
    def receipt_detail_page(receipt_id: str) -> None:
        try:
            rid = int(receipt_id)
        except ValueError:
            rid = -1

        client = context.client
        mark_dirty, mark_clean, is_dirty, guarded_navigate, clean_and_navigate = create_dirty_guard(
            f"atelierBuddyReceiptDirty_{rid}_{uuid.uuid4().hex}"
        )
        previous_area_path = _NAV_STATE.get("last_path")
        if isinstance(previous_area_path, str) and previous_area_path and previous_area_path != f"/belege/{rid}":
            _NAV_STATE["receipt_return_path"] = previous_area_path
        receipt_return_path = str(_NAV_STATE.get("receipt_return_path") or "/belege")
        if receipt_return_path == f"/belege/{rid}":
            receipt_return_path = "/belege"

        with _shell(
            "/belege",
            "Belegdetail",
            show_page_head=False,
            navigate_to=guarded_navigate,
            rerender_path=f"/belege/{rid}",
        ):
            if rid <= 0:
                with ui.card().classes("bm-card p-4 w-full"):
                    ui.label("Ungültige Beleg-ID")
                return

            with Session(engine) as session:
                receipt = session.exec(
                    select(Receipt)
                    .where(Receipt.id == rid)
                    .options(
                        selectinload(Receipt.supplier),
                        selectinload(Receipt.allocations).selectinload(CostAllocation.cost_type),
                        selectinload(Receipt.allocations).selectinload(CostAllocation.cost_subcategory),
                        selectinload(Receipt.allocations).selectinload(CostAllocation.project),
                    )
                ).first()
                suppliers = list(session.exec(select(Supplier).order_by(Supplier.name)).all())

                selected_cost_type_ids: list[int] = []
                selected_subcategory_ids: list[int] = []
                selected_project_ids: list[int] = []
                if receipt:
                    selected_cost_type_ids = sorted(
                        {
                            allocation.cost_type_id
                            for allocation in receipt.allocations
                            if isinstance(allocation.cost_type_id, int)
                        }
                    )
                    selected_subcategory_ids = sorted(
                        {
                            allocation.cost_subcategory_id
                            for allocation in receipt.allocations
                            if isinstance(allocation.cost_subcategory_id, int)
                        }
                    )
                    selected_project_ids = sorted(
                        {
                            allocation.project_id
                            for allocation in receipt.allocations
                            if isinstance(allocation.project_id, int)
                        }
                    )

                cost_type_stmt = select(CostType).order_by(CostType.name)
                if selected_cost_type_ids:
                    cost_type_stmt = cost_type_stmt.where(
                        or_(CostType.active.is_(True), CostType.id.in_(selected_cost_type_ids))
                    )
                else:
                    cost_type_stmt = cost_type_stmt.where(CostType.active.is_(True))
                cost_types = list(session.exec(cost_type_stmt).all())

                cost_subcategory_stmt = select(CostSubcategory).order_by(
                    CostSubcategory.cost_type_id, CostSubcategory.name
                )
                if selected_subcategory_ids:
                    cost_subcategory_stmt = cost_subcategory_stmt.where(
                        or_(CostSubcategory.active.is_(True), CostSubcategory.id.in_(selected_subcategory_ids))
                    )
                else:
                    cost_subcategory_stmt = cost_subcategory_stmt.where(CostSubcategory.active.is_(True))
                cost_subcategories = list(session.exec(cost_subcategory_stmt).all())

            if not receipt:
                with ui.card().classes("bm-card p-4 w-full"):
                    ui.label("Beleg nicht gefunden")
                return

            preview_url = to_files_url(receipt.archive_path)
            preview_is_pdf = bool(receipt.archive_path and Path(receipt.archive_path).suffix.lower() == ".pdf")
            is_deleted = receipt.deleted_at is not None

            with ui.card().classes("bm-card bm-detail-card p-4 w-full"):
                with ui.row().classes("bm-detail-toolbar w-full items-center justify-between"):
                    with ui.row().classes("items-center gap-2"):
                        cancel_btn = ui.button(
                            icon="close",
                            on_click=lambda path=receipt_return_path: guarded_navigate(path),
                        ).props("flat round dense").classes("bm-icon-action-btn")
                        cancel_btn.tooltip("Abbrechen")
                        if preview_url:
                            open_btn = ui.button(
                                icon="open_in_new",
                                on_click=lambda url=preview_url: open_in_new_tab(url),
                            ).props("flat round dense").classes("bm-icon-action-btn")
                            open_btn.tooltip("Original in neuem Tab öffnen")
                    with ui.row().classes("items-center gap-2"):
                        if not is_deleted:
                            delete_btn = ui.button(
                                icon="delete_outline",
                                on_click=lambda rid=receipt.id: _detail_move_to_deleted(rid),
                            ).props("flat round dense").classes("bm-icon-action-btn bm-icon-action-btn--danger")
                            delete_btn.tooltip("In Gelöschte Belege")
                            save_btn = ui.button(
                                icon="save",
                                on_click=lambda: asyncio.create_task(_detail_save()),
                            ).props("flat round dense").classes("bm-icon-action-btn bm-icon-action-btn--primary")
                            save_btn.tooltip("Speichern")
                        else:
                            restore_btn = ui.button(
                                icon="restore_from_trash",
                                on_click=lambda rid=receipt.id: _detail_restore(rid),
                            ).props("flat round dense").classes("bm-icon-action-btn bm-icon-action-btn--success")
                            restore_btn.tooltip("Wiederherstellen")
                ui.label(f"Beleg #{receipt.id}").classes("text-xl font-semibold mb-2")

                with ui.element("div").classes("bm-detail-grid w-full"):
                    with ui.column().classes("bm-detail-preview gap-2"):
                        if preview_url and preview_is_pdf:
                            viewer_id = f"bm_pdf_{uuid.uuid4().hex}"
                            viewport_id = f"{viewer_id}_viewport"
                            canvas_id = f"{viewer_id}_canvas"
                            status_id = f"{viewer_id}_status"
                            page_label_id = f"{viewer_id}_page"
                            zoom_label_id = f"{viewer_id}_zoom"
                            prev_btn_id = f"{viewer_id}_prev"
                            next_btn_id = f"{viewer_id}_next"
                            zoom_out_btn_id = f"{viewer_id}_zoom_out"
                            zoom_in_btn_id = f"{viewer_id}_zoom_in"
                            fit_btn_id = f"{viewer_id}_fit"

                            ui.html(
                                f"""
                                <div class="bm-pdf-viewer">
                                  <div class="bm-pdf-toolbar">
                                    <button id="{prev_btn_id}" type="button">◀</button>
                                    <span id="{page_label_id}" class="bm-pdf-label">Seite 1 / 1</span>
                                    <button id="{next_btn_id}" type="button">▶</button>
                                    <span class="bm-pdf-spacer"></span>
                                    <button id="{zoom_out_btn_id}" type="button">−</button>
                                    <span id="{zoom_label_id}" class="bm-pdf-label">100%</span>
                                    <button id="{zoom_in_btn_id}" type="button">+</button>
                                    <button id="{fit_btn_id}" type="button">Fit</button>
                                  </div>
                                  <div id="{viewport_id}" class="bm-pdf-viewport">
                                    <canvas id="{canvas_id}"></canvas>
                                  </div>
                                  <div id="{status_id}" class="bm-pdf-status">PDF wird geladen ...</div>
                                </div>
                                """
                            ).classes("w-full bm-detail-preview-frame")
                            ui.run_javascript(
                                f"""
                                (async () => {{
                                  const pdfjsLib = window.pdfjsLib;
                                  const statusEl = document.getElementById({json.dumps(status_id)});
                                  const viewportEl = document.getElementById({json.dumps(viewport_id)});
                                  const canvas = document.getElementById({json.dumps(canvas_id)});
                                  const pageLabel = document.getElementById({json.dumps(page_label_id)});
                                  const zoomLabel = document.getElementById({json.dumps(zoom_label_id)});
                                  const prevBtn = document.getElementById({json.dumps(prev_btn_id)});
                                  const nextBtn = document.getElementById({json.dumps(next_btn_id)});
                                  const zoomOutBtn = document.getElementById({json.dumps(zoom_out_btn_id)});
                                  const zoomInBtn = document.getElementById({json.dumps(zoom_in_btn_id)});
                                  const fitBtn = document.getElementById({json.dumps(fit_btn_id)});

                                  if (!pdfjsLib || !statusEl || !viewportEl || !canvas) {{
                                    if (statusEl) statusEl.textContent = 'PDF-Vorschau nicht verfügbar.';
                                    return;
                                  }}

                                  pdfjsLib.GlobalWorkerOptions.workerSrc = '/assets/pdfjs/pdf.worker.min.js';
                                  const context = canvas.getContext('2d');
                                  const sourceUrl = {json.dumps(preview_url)};

                                  let pdfDoc = null;
                                  let pageNum = 1;
                                  let scale = 1.0;

                                  const renderPage = async () => {{
                                    if (!pdfDoc) return;
                                    const page = await pdfDoc.getPage(pageNum);
                                    const viewport = page.getViewport({{ scale }});

                                    canvas.width = Math.floor(viewport.width);
                                    canvas.height = Math.floor(viewport.height);
                                    canvas.style.display = 'block';

                                    await page.render({{ canvasContext: context, viewport }}).promise;

                                    pageLabel.textContent = `Seite ${{pageNum}} / ${{pdfDoc.numPages}}`;
                                    zoomLabel.textContent = `${{Math.round(scale * 100)}}%`;
                                    statusEl.textContent = '';
                                    prevBtn.disabled = pageNum <= 1;
                                    nextBtn.disabled = pageNum >= pdfDoc.numPages;
                                  }};

                                  const fitWidth = async () => {{
                                    if (!pdfDoc) return;
                                    const page = await pdfDoc.getPage(pageNum);
                                    const raw = page.getViewport({{ scale: 1 }});
                                    const available = Math.max(viewportEl.clientWidth - 24, 280);
                                    scale = Math.max(0.5, Math.min(3.0, available / raw.width));
                                    await renderPage();
                                  }};

                                  prevBtn?.addEventListener('click', async () => {{
                                    if (pageNum <= 1) return;
                                    pageNum -= 1;
                                    await renderPage();
                                  }});
                                  nextBtn?.addEventListener('click', async () => {{
                                    if (!pdfDoc || pageNum >= pdfDoc.numPages) return;
                                    pageNum += 1;
                                    await renderPage();
                                  }});
                                  zoomOutBtn?.addEventListener('click', async () => {{
                                    scale = Math.max(0.5, scale - 0.1);
                                    await renderPage();
                                  }});
                                  zoomInBtn?.addEventListener('click', async () => {{
                                    scale = Math.min(3.0, scale + 0.1);
                                    await renderPage();
                                  }});
                                  fitBtn?.addEventListener('click', fitWidth);

                                  try {{
                                    statusEl.textContent = 'PDF wird geladen ...';
                                    const loadingTask = pdfjsLib.getDocument({{ url: sourceUrl }});
                                    pdfDoc = await loadingTask.promise;
                                    await fitWidth();
                                  }} catch (err) {{
                                    console.error('PDF render error', err);
                                    statusEl.textContent = 'PDF-Vorschau konnte nicht geladen werden.';
                                  }}
                                }})();
                                """
                            )
                            ui.label("Falls nötig: über den Icon-Button in neuem Tab öffnen.").classes(
                                "text-xs text-slate-500"
                            )
                        elif preview_url:
                            image_viewer_id = f"bm_img_{uuid.uuid4().hex}"
                            image_viewport_id = f"{image_viewer_id}_viewport"
                            image_stage_id = f"{image_viewer_id}_stage"
                            image_status_id = f"{image_viewer_id}_status"
                            image_zoom_label_id = f"{image_viewer_id}_zoom"
                            image_zoom_out_id = f"{image_viewer_id}_zoom_out"
                            image_zoom_in_id = f"{image_viewer_id}_zoom_in"
                            image_actual_id = f"{image_viewer_id}_actual"
                            image_fit_id = f"{image_viewer_id}_fit"

                            ui.html(
                                f"""
                                <div class="bm-image-viewer">
                                  <div class="bm-image-toolbar">
                                    <button id="{image_zoom_out_id}" type="button">−</button>
                                    <span id="{image_zoom_label_id}" class="bm-image-label">100%</span>
                                    <button id="{image_zoom_in_id}" type="button">+</button>
                                    <button id="{image_actual_id}" type="button">100%</button>
                                    <button id="{image_fit_id}" type="button">Fit</button>
                                    <span class="bm-image-spacer"></span>
                                  </div>
                                  <div id="{image_viewport_id}" class="bm-image-viewport">
                                    <img id="{image_stage_id}" class="bm-image-stage" src="{preview_url}" alt="Belegvorschau" />
                                  </div>
                                  <div id="{image_status_id}" class="bm-image-status">Bild wird geladen ...</div>
                                </div>
                                """
                            ).classes("w-full bm-detail-preview-frame")
                            ui.run_javascript(
                                f"""
                                (() => {{
                                  const viewportEl = document.getElementById({json.dumps(image_viewport_id)});
                                  const imageEl = document.getElementById({json.dumps(image_stage_id)});
                                  const statusEl = document.getElementById({json.dumps(image_status_id)});
                                  const zoomLabel = document.getElementById({json.dumps(image_zoom_label_id)});
                                  const zoomOutBtn = document.getElementById({json.dumps(image_zoom_out_id)});
                                  const zoomInBtn = document.getElementById({json.dumps(image_zoom_in_id)});
                                  const actualBtn = document.getElementById({json.dumps(image_actual_id)});
                                  const fitBtn = document.getElementById({json.dumps(image_fit_id)});

                                  if (!viewportEl || !imageEl || !statusEl || !zoomLabel) return;

                                  let naturalWidth = 0;
                                  let naturalHeight = 0;
                                  let scale = 1;

                                  const clamp = (value) => Math.max(0.2, Math.min(6, value));

                                  const applyScale = () => {{
                                    if (!naturalWidth || !naturalHeight) return;
                                    const width = Math.max(1, Math.round(naturalWidth * scale));
                                    imageEl.style.width = `${{width}}px`;
                                    imageEl.style.height = 'auto';
                                    zoomLabel.textContent = `${{Math.round(scale * 100)}}%`;
                                  }};

                                  const fitWidth = () => {{
                                    if (!naturalWidth) return;
                                    const available = Math.max(viewportEl.clientWidth - 24, 140);
                                    scale = clamp(available / naturalWidth);
                                    applyScale();
                                  }};

                                  zoomOutBtn?.addEventListener('click', () => {{
                                    scale = clamp(scale - 0.1);
                                    applyScale();
                                  }});

                                  zoomInBtn?.addEventListener('click', () => {{
                                    scale = clamp(scale + 0.1);
                                    applyScale();
                                  }});

                                  actualBtn?.addEventListener('click', () => {{
                                    scale = 1;
                                    applyScale();
                                  }});

                                  fitBtn?.addEventListener('click', () => fitWidth());

                                  const initialize = () => {{
                                    naturalWidth = imageEl.naturalWidth || 0;
                                    naturalHeight = imageEl.naturalHeight || 0;
                                    if (!naturalWidth || !naturalHeight) {{
                                      statusEl.textContent = 'Bildvorschau konnte nicht geladen werden.';
                                      return;
                                    }}
                                    fitWidth();
                                    statusEl.textContent = '';
                                  }};

                                  if (imageEl.complete) {{
                                    initialize();
                                  }} else {{
                                    imageEl.addEventListener('load', initialize, {{ once: true }});
                                    imageEl.addEventListener(
                                      'error',
                                      () => {{
                                        statusEl.textContent = 'Bildvorschau konnte nicht geladen werden.';
                                      }},
                                      {{ once: true }},
                                    );
                                  }}

                                  window.addEventListener('resize', () => {{
                                    if (!naturalWidth) return;
                                    if (scale < 1.05) fitWidth();
                                  }});
                                }})();
                                """
                            )
                        else:
                            with ui.element("div").classes(
                                "w-full h-[360px] rounded-xl bg-slate-100 flex items-center justify-center"
                            ):
                                ui.icon("description", size="52px")

                    with ui.column().classes("bm-detail-form gap-3"):
                        completeness_label = ui.label("").classes("text-sm")
                        missing_fields_label = ui.label("").classes("text-xs")
                        if is_deleted:
                            ui.label("Dieser Beleg liegt in Gelöschte Belege.").classes("text-sm text-amber-700")

                        date_input = ui.input(
                            "Belegdatum",
                            value=receipt.doc_date.isoformat() if receipt.doc_date else "",
                        ).props("type=date clearable").classes("w-full")
                        supplier_map = {s.id: s.name for s in suppliers if s.id is not None}
                        with ui.row().classes("bm-allocation-line"):
                            supplier_input = ui.select(
                                supplier_map,
                                value=receipt.supplier_id,
                                label="Anbieter",
                                clearable=True,
                            ).props("use-input input-debounce=0").classes("bm-allocation-main-field")
                            supplier_add_btn = ui.button(
                                icon="add",
                                on_click=lambda: open_quick_supplier_dialog(),
                            ).props("flat").classes("bm-inline-create-btn")
                            supplier_add_btn.tooltip("Neuen Anbieter anlegen")
                        document_type_value = (
                            receipt.document_type
                            if receipt.document_type in {DOC_TYPE_INVOICE, DOC_TYPE_CREDIT_NOTE}
                            else DOC_TYPE_INVOICE
                        )
                        with ui.row().classes("w-full items-center bm-form-row"):
                            document_type_input = ui.toggle(
                                {DOC_TYPE_INVOICE: "Rechnung", DOC_TYPE_CREDIT_NOTE: "Gutschrift"},
                                value=document_type_value,
                            ).props("unelevated no-caps").classes("bm-view-mode-btn bm-doc-type-toggle bm-form-field")

                        gross_default = ""
                        if receipt.amount_gross_cents is not None:
                            gross_default = f"{(Decimal(receipt.amount_gross_cents) / Decimal('100')):.2f}".replace(
                                ".",
                                ",",
                            )
                        vat_default = (
                            str(receipt.vat_rate_percent).replace(".", ",")
                            if receipt.vat_rate_percent is not None
                            else str(settings.default_vat_rate_percent).replace(".", ",")
                        )

                        with ui.row().classes("w-full gap-2 items-end wrap bm-form-row"):
                            gross_input = ui.input(
                                f"Brutto ({settings.default_currency})",
                                value=gross_default,
                            ).props("input-debounce=0").classes("min-w-0 flex-1 bm-form-field")
                            vat_input = ui.input("USt-Satz (%)", value=vat_default).props("input-debounce=0").classes(
                                "w-36 bm-form-field"
                            )
                            net_input = ui.input(
                                f"Netto ({settings.default_currency})",
                                value=_format_cents(receipt.amount_net_cents, settings.default_currency),
                            ).props("readonly").classes("min-w-0 flex-1 bm-form-field")
                        notes_input = ui.textarea("Notizen (optional)", value=receipt.notes or "").props(
                            'clearable rows=3 input-style="min-height: 5.5rem; resize: vertical;"'
                        ).classes("w-full bm-form-field")

                        cost_type_select_options = {item.id: item.name for item in cost_types if item.id is not None}
                        project_map = project_options(active_only=True, include_ids=selected_project_ids)
                        subcategories_by_type: dict[int, list[CostSubcategory]] = {}
                        subcategory_parent_by_id = {
                            item.id: item.cost_type_id
                            for item in cost_subcategories
                            if item.id is not None
                        }
                        default_subcategory_by_type: dict[int, int] = {}
                        for subcategory in cost_subcategories:
                            subcategories_by_type.setdefault(subcategory.cost_type_id, []).append(subcategory)
                            if subcategory.is_system_default and subcategory.id is not None:
                                default_subcategory_by_type[subcategory.cost_type_id] = subcategory.id
                        for cost_type in cost_types:
                            if cost_type.id is None:
                                continue
                            if cost_type.id not in default_subcategory_by_type:
                                for subcategory in subcategories_by_type.get(cost_type.id, []):
                                    if subcategory.id is not None:
                                        default_subcategory_by_type[cost_type.id] = subcategory.id
                                        break

                        def subcategory_options_for_type(
                            cost_type_id: int | None,
                            include_subcategory_id: int | None = None,
                        ) -> dict[int, str]:
                            if cost_type_id is None:
                                return {}
                            items = [
                                item
                                for item in subcategories_by_type.get(cost_type_id, [])
                                if item.id is not None and (item.active or item.id == include_subcategory_id)
                            ]
                            items.sort(key=lambda item: (0 if item.is_system_default else 1, (item.name or "").casefold()))
                            return {
                                item.id: item.name
                                for item in items
                            }

                        def to_optional_int(value: Any) -> int | None:
                            if value is None:
                                return None
                            if isinstance(value, int):
                                return value if value > 0 else None
                            if isinstance(value, str):
                                text = value.strip()
                                if not text:
                                    return None
                                if text.isdigit():
                                    try:
                                        parsed = int(text)
                                        return parsed if parsed > 0 else None
                                    except ValueError:
                                        return None
                            return None

                        def reload_supplier_options(selected_id: int | None = None) -> None:
                            nonlocal suppliers, supplier_map
                            with Session(engine) as session:
                                suppliers = list(session.exec(select(Supplier).order_by(Supplier.name)).all())
                            supplier_map = {item.id: item.name for item in suppliers if item.id is not None}
                            current = to_optional_int(supplier_input.value)
                            next_value = None
                            if isinstance(selected_id, int) and selected_id in supplier_map:
                                next_value = selected_id
                            elif isinstance(current, int) and current in supplier_map:
                                next_value = current
                            supplier_input.set_options(supplier_map, value=next_value)

                        def reload_project_options(selected_id: int | None = None) -> None:
                            nonlocal project_map
                            include_ids = [
                                item
                                for item in [selected_id, *(to_optional_int(row.get("project_id")) for row in allocation_rows)]
                                if isinstance(item, int)
                            ]
                            project_map = project_options(active_only=True, include_ids=include_ids)

                        def open_quick_supplier_dialog() -> None:
                            with ui.dialog() as dialog, ui.card().classes("p-4 w-[480px] max-w-full"):
                                ui.label("Neuer Anbieter").classes("text-lg font-semibold")
                                name_input = ui.input("Anbietername").classes("w-full")
                                active_input = ui.checkbox("Aktiv", value=True)

                                def save_supplier() -> None:
                                    name = (name_input.value or "").strip()
                                    if not name:
                                        ui.notify("Anbietername fehlt", type="negative")
                                        return
                                    try:
                                        supplier, created = masterdata.create_or_update_supplier(
                                            name=name,
                                            active=bool(active_input.value),
                                        )
                                        supplier_id = supplier.id
                                        ui.notify(
                                            "Anbieter angelegt" if created else "Anbieter existierte bereits und wurde aktualisiert",
                                            type="positive",
                                        )
                                        reload_supplier_options(selected_id=supplier_id if isinstance(supplier_id, int) else None)
                                        refresh_completion_state()
                                        dialog.close()
                                    except Exception as exc:
                                        _notify_error("Anbieter konnte nicht angelegt werden", exc)

                                with ui.row().classes("w-full justify-end gap-2"):
                                    ui.button("Abbrechen", on_click=dialog.close).props("flat")
                                    ui.button("Anlegen", on_click=save_supplier).props("color=primary")
                            dialog.open()

                        def open_quick_project_dialog(row_target: dict[str, Any] | None = None) -> None:
                            with ui.dialog() as dialog, ui.card().classes("p-4 w-[520px] max-w-full"):
                                ui.label("Neues Projekt").classes("text-lg font-semibold")
                                name_input = ui.input("Projektname").classes("w-full")
                                price_input = ui.input(f"Preis ({settings.default_currency}, optional)").classes("w-full")
                                created_on_input = ui.input("Erschaffen am (optional)").props("type=date clearable").classes("w-full")
                                active_input = ui.checkbox("Aktiv", value=True)

                                def save_project() -> None:
                                    name = (name_input.value or "").strip()
                                    if not name:
                                        ui.notify("Projektname fehlt", type="negative")
                                        return
                                    try:
                                        project, created = masterdata.create_or_update_project(
                                            name=name,
                                            active=bool(active_input.value),
                                            price_cents=_parse_money_to_cents(price_input.value),
                                            created_on=_parse_iso_date(created_on_input.value),
                                        )
                                        project_id = project.id
                                        ui.notify(
                                            "Projekt angelegt" if created else "Projekt existierte bereits und wurde aktualisiert",
                                            type="positive",
                                        )
                                        reload_project_options(selected_id=project_id if isinstance(project_id, int) else None)
                                        if row_target is not None and isinstance(project_id, int):
                                            row_target["project_id"] = project_id
                                        render_allocation_editor()
                                        refresh_allocation_summary()
                                        refresh_completion_state()
                                        dialog.close()
                                    except Exception as exc:
                                        _notify_error("Projekt konnte nicht angelegt werden", exc)

                                with ui.row().classes("w-full justify-end gap-2"):
                                    ui.button("Abbrechen", on_click=dialog.close).props("flat")
                                    ui.button("Anlegen", on_click=save_project).props("color=primary")
                            dialog.open()

                        def ensure_subcategory_for_row(row: dict[str, Any]) -> None:
                            cost_type_id = row.get("cost_type_id")
                            if not isinstance(cost_type_id, int):
                                row["cost_subcategory_id"] = None
                                return
                            current = row.get("cost_subcategory_id")
                            options = subcategory_options_for_type(
                                cost_type_id,
                                include_subcategory_id=current if isinstance(current, int) else None,
                            )
                            if isinstance(current, int) and current in options:
                                return
                            row["cost_subcategory_id"] = default_subcategory_by_type.get(cost_type_id)

                        def cents_to_text(cents: int | None) -> str:
                            if cents is None:
                                return ""
                            return f"{(Decimal(cents) / Decimal('100')):.2f}".replace(".", ",")

                        def current_document_type() -> str:
                            value = str(document_type_input.value or DOC_TYPE_INVOICE).strip().lower()
                            if value not in {DOC_TYPE_INVOICE, DOC_TYPE_CREDIT_NOTE}:
                                return DOC_TYPE_INVOICE
                            return value

                        def signed_display_text(cents: int | None) -> str:
                            if cents is None:
                                return ""
                            return cents_to_text(cents)

                        def normalize_amount_text_for_document_type(
                            raw_value: Any,
                            *,
                            document_type: str,
                            prefill_minus_if_empty: bool = False,
                        ) -> str:
                            raw_text = str(raw_value or "").strip()
                            if not raw_text:
                                if document_type == DOC_TYPE_CREDIT_NOTE and prefill_minus_if_empty:
                                    return "-"
                                return ""
                            if raw_text in {"-", "+"}:
                                return "-" if document_type == DOC_TYPE_CREDIT_NOTE else ""
                            try:
                                parsed_cents = _parse_money_to_cents(raw_text, allow_negative=True)
                            except ValueError:
                                if document_type == DOC_TYPE_CREDIT_NOTE:
                                    return raw_text if raw_text.startswith("-") else f"-{raw_text.lstrip('+')}"
                                return raw_text[1:] if raw_text.startswith("-") else raw_text
                            if parsed_cents is None:
                                return "-" if document_type == DOC_TYPE_CREDIT_NOTE and prefill_minus_if_empty else ""
                            if document_type == DOC_TYPE_CREDIT_NOTE:
                                parsed_cents = -abs(parsed_cents)
                            else:
                                parsed_cents = abs(parsed_cents)
                            return signed_display_text(parsed_cents)

                        def flip_amount_text_sign(raw_value: Any) -> str:
                            raw_text = str(raw_value or "").strip()
                            if not raw_text:
                                return ""
                            if raw_text in {"-", "+"}:
                                return ""
                            try:
                                parsed_cents = _parse_money_to_cents(raw_text, allow_negative=True)
                            except ValueError:
                                if raw_text.startswith("-"):
                                    return raw_text[1:]
                                return f"-{raw_text.lstrip('+')}"
                            if parsed_cents is None:
                                return ""
                            return signed_display_text(-parsed_cents)

                        allocation_rows: list[dict[str, Any]] = []
                        if receipt.allocations:
                            for allocation in sorted(receipt.allocations, key=lambda item: item.position):
                                allocation_rows.append(
                                    {
                                        "cost_type_id": allocation.cost_type_id,
                                        "cost_subcategory_id": allocation.cost_subcategory_id,
                                        "project_id": allocation.project_id,
                                        "amount_text": cents_to_text(allocation.amount_cents),
                                    }
                                )
                        if not allocation_rows:
                            allocation_rows.append(
                                {
                                    "cost_type_id": None,
                                    "cost_subcategory_id": None,
                                    "project_id": None,
                                    "amount_text": cents_to_text(receipt.amount_gross_cents),
                                }
                            )
                        for row in allocation_rows:
                            ensure_subcategory_for_row(row)
                        split_enabled = len(allocation_rows) > 1
                        allocation_editor = ui.column().classes("w-full gap-2")
                        allocation_summary = ui.label("").classes("text-xs")
                        allocation_status_label = ui.label("").classes("text-xs")
                        allocation_controls: list[dict[str, Any]] = []
                        completion_service = ReceiptCompletionService()

                        def parse_vat_rate_input(*, strict: bool) -> float | None:
                            vat_raw = (vat_input.value or "").strip()
                            if not vat_raw:
                                return None
                            try:
                                vat_rate = float(vat_raw.replace(",", "."))
                            except ValueError as exc:
                                if strict:
                                    raise ValueError("Ungültiger USt-Satz") from exc
                                return None
                            if vat_rate < 0:
                                if strict:
                                    raise ValueError("USt-Satz darf nicht negativ sein")
                                return None
                            return vat_rate

                        def build_allocation_payload(*, strict: bool) -> list[AllocationInput]:
                            payload: list[AllocationInput] = []
                            for index, control in enumerate(allocation_controls):
                                cost_type_value = to_optional_int(control["cost_type_input"].value)
                                cost_subcategory_value = to_optional_int(control["cost_subcategory_input"].value)
                                project_value = to_optional_int(control["project_input"].value)
                                raw_amount_value = gross_input.value if control["mode"] == "standard" else control["amount_input"].value
                                try:
                                    amount_cents = _parse_money_to_cents(raw_amount_value, allow_negative=True)
                                except ValueError:
                                    if strict:
                                        raise
                                    amount_cents = None
                                payload.append(
                                    AllocationInput(
                                        cost_type_id=cost_type_value,
                                        cost_subcategory_id=cost_subcategory_value,
                                        project_id=project_value,
                                        cost_area_id=None,
                                        amount_cents=amount_cents,
                                        position=index + 1,
                                    )
                                )
                            return payload

                        def build_receipt_save_input(*, strict: bool) -> ReceiptSaveInput:
                            try:
                                gross_cents = _parse_money_to_cents(gross_input.value, allow_negative=True)
                            except ValueError:
                                if strict:
                                    raise
                                gross_cents = None
                            return ReceiptSaveInput(
                                doc_date=_parse_iso_date(date_input.value),
                                supplier_id=to_optional_int(supplier_input.value),
                                amount_gross_cents=gross_cents,
                                vat_rate_percent=parse_vat_rate_input(strict=strict),
                                amount_net_cents=None,
                                notes=notes_input.value,
                                document_type=current_document_type(),
                                allocations=build_allocation_payload(strict=strict),
                            )

                        def current_completion_result() -> Any:
                            snapshot = build_receipt_save_input(strict=False)
                            normalized_vat_rate = snapshot.vat_rate_percent
                            if snapshot.amount_gross_cents is None:
                                normalized_vat_rate = None
                            normalized_snapshot = completion_service.with_computed_net(
                                ReceiptSaveInput(
                                    doc_date=snapshot.doc_date,
                                    supplier_id=snapshot.supplier_id,
                                    amount_gross_cents=snapshot.amount_gross_cents,
                                    vat_rate_percent=normalized_vat_rate,
                                    amount_net_cents=None,
                                    notes=snapshot.notes,
                                    document_type=snapshot.document_type,
                                    allocations=snapshot.allocations,
                                )
                            )
                            return completion_service.evaluate_snapshot(
                                normalized_snapshot,
                                subcategory_type_ids=subcategory_parent_by_id,
                            )

                        def refresh_net_preview() -> None:
                            try:
                                gross_cents = _parse_money_to_cents(gross_input.value, allow_negative=True)
                                if gross_cents is None:
                                    net_input.value = "-"
                                    return
                                vat_raw = (vat_input.value or "").strip()
                                if not vat_raw:
                                    net_input.value = "-"
                                    return
                                vat_rate = float(vat_raw.replace(",", "."))
                                if vat_rate < 0:
                                    net_input.value = "Ungültiger USt-Satz"
                                    return
                                net_cents = _compute_net_cents(gross_cents, vat_rate)
                                net_input.value = _format_cents(net_cents, settings.default_currency)
                            except Exception:
                                net_input.value = "Ungültige Eingabe"

                        net_preview_task: asyncio.Task | None = None

                        def schedule_net_preview(delay_seconds: float = 0.3) -> None:
                            nonlocal net_preview_task
                            if net_preview_task and not net_preview_task.done():
                                net_preview_task.cancel()

                            async def delayed_preview() -> None:
                                try:
                                    await asyncio.sleep(delay_seconds)
                                except asyncio.CancelledError:
                                    return
                                refresh_net_preview()

                            net_preview_task = asyncio.create_task(delayed_preview())

                        def apply_document_type_sign_to_inputs(*, flip_existing: bool = False) -> None:
                            if flip_existing:
                                gross_input.value = flip_amount_text_sign(gross_input.value)
                                for row in allocation_rows:
                                    row["amount_text"] = flip_amount_text_sign(row.get("amount_text"))
                            else:
                                gross_input.value = normalize_amount_text_for_document_type(
                                    gross_input.value,
                                    document_type=current_document_type(),
                                    prefill_minus_if_empty=True,
                                )
                                for row in allocation_rows:
                                    row["amount_text"] = normalize_amount_text_for_document_type(
                                        row.get("amount_text"),
                                        document_type=current_document_type(),
                                        prefill_minus_if_empty=False,
                                    )

                        def normalize_gross_on_blur() -> None:
                            gross_input.value = normalize_amount_text_for_document_type(
                                gross_input.value,
                                document_type=current_document_type(),
                                prefill_minus_if_empty=True,
                            )
                            refresh_net_preview()
                            refresh_allocation_summary()
                            refresh_completion_state()

                        previous_document_type = current_document_type()

                        def on_document_type_change() -> None:
                            nonlocal previous_document_type
                            next_document_type = current_document_type()
                            if next_document_type == previous_document_type:
                                return
                            apply_document_type_sign_to_inputs(flip_existing=True)
                            previous_document_type = next_document_type
                            render_allocation_editor()
                            refresh_net_preview()
                            refresh_allocation_summary()
                            refresh_completion_state()

                        gross_input.on_value_change(
                            lambda _: (schedule_net_preview(), mark_dirty(), refresh_allocation_summary(), refresh_completion_state())
                        )
                        vat_input.on_value_change(
                            lambda _: (schedule_net_preview(), mark_dirty(), refresh_completion_state())
                        )
                        date_input.on_value_change(lambda _: (mark_dirty(), refresh_completion_state()))
                        supplier_input.on_value_change(lambda _: (mark_dirty(), refresh_completion_state()))
                        notes_input.on_value_change(lambda _: mark_dirty())
                        gross_input.on("keydown.enter", lambda _: refresh_net_preview())
                        vat_input.on("keydown.enter", lambda _: refresh_net_preview())
                        gross_input.on("blur", lambda _: normalize_gross_on_blur())
                        document_type_input.on_value_change(lambda _: on_document_type_change())
                        refresh_net_preview()

                        def refresh_allocation_summary() -> None:
                            mark_dirty()
                            snapshot = build_receipt_save_input(strict=False)
                            gross_cents = snapshot.amount_gross_cents
                            if gross_cents is None:
                                allocation_summary.text = "Kostenzuordnung: Brutto fehlt oder ist ungültig."
                                allocation_summary.classes("text-xs text-amber-700")
                                return
                            if not split_enabled:
                                allocation_summary.text = (
                                    f"Standardmodus: 1 Zuordnung mit 100% ({_format_cents(gross_cents, settings.default_currency)})."
                                )
                                allocation_summary.classes("text-xs text-slate-600")
                                return

                            _, diff = _allocation_total_and_diff_cents(
                                gross_cents,
                                [allocation.amount_cents for allocation in snapshot.allocations],
                                allow_negative=True,
                            )
                            if diff is None:
                                allocation_summary.text = "Kostenzuordnung: Brutto fehlt oder ist ungültig."
                                allocation_summary.classes("text-xs text-amber-700")
                                return
                            if diff == 0:
                                allocation_summary.text = "Kostenzuordnung vollständig."
                                allocation_summary.classes("text-xs text-green-700")
                            else:
                                is_credit_note = gross_cents < 0
                                remaining_missing = (diff > 0 and not is_credit_note) or (diff < 0 and is_credit_note)
                                if remaining_missing:
                                    allocation_summary.text = (
                                        f"Noch nicht zugeordnet: {_format_cents(abs(diff), settings.default_currency)}."
                                    )
                                    allocation_summary.classes("text-xs text-amber-700")
                                else:
                                    allocation_summary.text = (
                                        f"Zu viel zugeordnet: {_format_cents(abs(diff), settings.default_currency)}."
                                    )
                                    allocation_summary.classes("text-xs text-red-600")

                        def refresh_completion_state() -> None:
                            result = current_completion_result()
                            completeness_label.text = (
                                "Vollständigkeit: Vollständig"
                                if result.is_complete
                                else "Vollständigkeit: Pflichtangaben fehlen"
                            )
                            completeness_label.classes(remove="text-green-700 text-amber-700")
                            completeness_label.classes(add="text-green-700" if result.is_complete else "text-amber-700")
                            missing_fields_label.text = (
                                ""
                                if result.is_complete
                                else f"Fehlt: {', '.join(result.missing_fields)}"
                            )
                            missing_fields_label.classes(remove="hidden")
                            if result.is_complete:
                                missing_fields_label.classes(add="hidden")
                            allocation_status_label.text = (
                                "Zuordnungsstatus: offiziell gebucht."
                                if result.allocation_status_to_persist == COST_ALLOCATION_STATUS_POSTED
                                else "Zuordnungsstatus: Entwurf. Kosten gelten noch nicht als offiziell gebucht."
                            )
                            allocation_status_label.classes(remove="text-green-700 text-amber-700")
                            allocation_status_label.classes(
                                add="text-green-700"
                                if result.allocation_status_to_persist == COST_ALLOCATION_STATUS_POSTED
                                else "text-amber-700"
                            )

                        def ensure_standard_single_row() -> None:
                            nonlocal allocation_rows
                            if not allocation_rows:
                                allocation_rows = [
                                    {
                                        "cost_type_id": None,
                                        "cost_subcategory_id": None,
                                        "project_id": None,
                                        "amount_text": "",
                                    }
                                ]
                            if len(allocation_rows) > 1:
                                allocation_rows = [allocation_rows[0]]
                            gross_cents = _parse_money_to_cents(gross_input.value, allow_negative=True)
                            allocation_rows[0]["amount_text"] = cents_to_text(gross_cents)
                            ensure_subcategory_for_row(allocation_rows[0])

                        def render_allocation_editor() -> None:
                            allocation_editor.clear()
                            allocation_controls.clear()
                            with allocation_editor:
                                with ui.row().classes("w-full items-center justify-between"):
                                    ui.label("Kostenzuordnung").classes("text-sm font-semibold")
                                    split_toggle = ui.switch("Aufteilung aktivieren", value=split_enabled).classes(
                                        "bm-inline-switch"
                                    )
                                    split_toggle.props("color=primary")

                                def on_split_toggle() -> None:
                                    nonlocal split_enabled
                                    split_enabled = bool(split_toggle.value)
                                    if not split_enabled:
                                        ensure_standard_single_row()
                                    render_allocation_editor()
                                    refresh_allocation_summary()
                                    refresh_completion_state()

                                split_toggle.on("update:model-value", lambda _: on_split_toggle())

                                if not split_enabled:
                                    ensure_standard_single_row()
                                    row = allocation_rows[0]
                                    with ui.column().classes("w-full gap-2"):
                                        with ui.row().classes("bm-allocation-line"):
                                            cost_type_input = ui.select(
                                                cost_type_select_options,
                                                value=row.get("cost_type_id"),
                                                label="Kostenkategorie",
                                                clearable=True,
                                            ).props("use-input input-debounce=0").classes("bm-allocation-main-field")
                                            ui.input(
                                                "Anteil",
                                                value="100%",
                                            ).props("readonly").classes("bm-allocation-side-field")

                                        subcategory_input = ui.select(
                                            subcategory_options_for_type(
                                                int(row.get("cost_type_id")) if row.get("cost_type_id") else None,
                                                include_subcategory_id=(
                                                    int(row.get("cost_subcategory_id"))
                                                    if row.get("cost_subcategory_id")
                                                    else None
                                                ),
                                            ),
                                            value=row.get("cost_subcategory_id"),
                                            label="Unterkategorie",
                                            clearable=True,
                                        ).props("use-input input-debounce=0").classes("w-full")
                                        with ui.row().classes("bm-allocation-line"):
                                            project_input = ui.select(
                                                project_map,
                                                value=row.get("project_id"),
                                                label="Projekt (optional)",
                                                clearable=True,
                                            ).props("use-input input-debounce=0").classes("bm-allocation-main-field")
                                            project_add_btn = ui.button(
                                                icon="add",
                                                on_click=lambda row_ref=row: open_quick_project_dialog(row_ref),
                                            ).props("flat").classes("bm-inline-create-btn")
                                            project_add_btn.tooltip("Neues Projekt anlegen")
                                    allocation_controls.append(
                                        {
                                            "mode": "standard",
                                            "cost_type_input": cost_type_input,
                                            "cost_subcategory_input": subcategory_input,
                                            "project_input": project_input,
                                            "amount_input": None,
                                        }
                                    )

                                    def update_standard_fields() -> None:
                                        row["cost_type_id"] = to_optional_int(cost_type_input.value)
                                        ensure_subcategory_for_row(row)
                                        next_subcategory_options = subcategory_options_for_type(
                                            row.get("cost_type_id") if isinstance(row.get("cost_type_id"), int) else None,
                                            include_subcategory_id=(
                                                row.get("cost_subcategory_id")
                                                if isinstance(row.get("cost_subcategory_id"), int)
                                                else None
                                            ),
                                        )
                                        subcategory_input.set_options(
                                            next_subcategory_options,
                                            value=row.get("cost_subcategory_id"),
                                        )
                                        row["cost_subcategory_id"] = to_optional_int(subcategory_input.value)
                                        row["project_id"] = to_optional_int(project_input.value)

                                    def update_subcategory() -> None:
                                        row["cost_subcategory_id"] = to_optional_int(subcategory_input.value)
                                        row["project_id"] = to_optional_int(project_input.value)

                                    cost_type_input.on_value_change(
                                        lambda _: (update_standard_fields(), refresh_allocation_summary(), refresh_completion_state())
                                    )
                                    subcategory_input.on_value_change(
                                        lambda _: (update_subcategory(), refresh_allocation_summary(), refresh_completion_state())
                                    )
                                    project_input.on_value_change(
                                        lambda _: (update_subcategory(), refresh_allocation_summary(), refresh_completion_state())
                                    )
                                else:
                                    for idx, row in enumerate(allocation_rows):
                                        with ui.card().classes("bm-card p-2 w-full"):
                                            with ui.column().classes("w-full gap-2"):
                                                with ui.row().classes("bm-allocation-line"):
                                                    cost_type_input = ui.select(
                                                        cost_type_select_options,
                                                        value=row.get("cost_type_id"),
                                                        label="Kostenkategorie",
                                                        clearable=True,
                                                    ).props("use-input input-debounce=0").classes("bm-allocation-main-field")

                                                    amount_input = ui.input(
                                                        f"Betrag ({settings.default_currency})",
                                                        value=row.get("amount_text") or "",
                                                    ).props("input-debounce=0").classes("bm-allocation-side-field")

                                                subcategory_input = ui.select(
                                                    subcategory_options_for_type(
                                                        int(row.get("cost_type_id")) if row.get("cost_type_id") else None,
                                                        include_subcategory_id=(
                                                            int(row.get("cost_subcategory_id"))
                                                            if row.get("cost_subcategory_id")
                                                            else None
                                                        ),
                                                    ),
                                                    value=row.get("cost_subcategory_id"),
                                                    label="Unterkategorie",
                                                    clearable=True,
                                                ).props("use-input input-debounce=0").classes("w-full")

                                                with ui.row().classes("bm-allocation-line"):
                                                    project_input = ui.select(
                                                        project_map,
                                                        value=row.get("project_id"),
                                                        label="Projekt (optional)",
                                                        clearable=True,
                                                    ).props("use-input input-debounce=0").classes("bm-allocation-main-field")
                                                    project_add_btn = ui.button(
                                                        icon="add",
                                                        on_click=lambda row_ref=row: open_quick_project_dialog(row_ref),
                                                    ).props("flat").classes("bm-inline-create-btn")
                                                    project_add_btn.tooltip("Neues Projekt anlegen")
                                                if len(allocation_rows) > 1:
                                                    ui.button(
                                                        icon="remove_circle",
                                                        on_click=lambda i=idx: remove_allocation_row(i),
                                                    ).props("flat round dense color=negative")
                                            allocation_controls.append(
                                                {
                                                    "mode": "split",
                                                    "cost_type_input": cost_type_input,
                                                    "cost_subcategory_input": subcategory_input,
                                                    "project_input": project_input,
                                                    "amount_input": amount_input,
                                                }
                                            )

                                            def update_split_cost_type(
                                                row_ref: dict[str, Any] = row,
                                                cost_type_ref: ui.select = cost_type_input,
                                                subcategory_ref: ui.select = subcategory_input,
                                                project_ref: ui.select = project_input,
                                            ) -> None:
                                                row_ref["cost_type_id"] = to_optional_int(cost_type_ref.value)
                                                ensure_subcategory_for_row(row_ref)
                                                next_subcategory_options = subcategory_options_for_type(
                                                    row_ref.get("cost_type_id")
                                                    if isinstance(row_ref.get("cost_type_id"), int)
                                                    else None,
                                                    include_subcategory_id=(
                                                        row_ref.get("cost_subcategory_id")
                                                        if isinstance(row_ref.get("cost_subcategory_id"), int)
                                                        else None
                                                    ),
                                                )
                                                subcategory_ref.set_options(
                                                    next_subcategory_options,
                                                    value=row_ref.get("cost_subcategory_id"),
                                                )
                                                row_ref["cost_subcategory_id"] = to_optional_int(subcategory_ref.value)
                                                row_ref["project_id"] = to_optional_int(project_ref.value)

                                            def update_split_subcategory_project(
                                                row_ref: dict[str, Any] = row,
                                                subcategory_ref: ui.select = subcategory_input,
                                                project_ref: ui.select = project_input,
                                            ) -> None:
                                                row_ref["cost_subcategory_id"] = to_optional_int(subcategory_ref.value)
                                                row_ref["project_id"] = to_optional_int(project_ref.value)

                                            def update_split_amount(
                                                raw_value: Any,
                                                row_ref: dict[str, Any] = row,
                                            ) -> None:
                                                row_ref["amount_text"] = str(raw_value or "")

                                            cost_type_input.on_value_change(
                                                lambda _, fn=update_split_cost_type: (
                                                    fn(),
                                                    refresh_allocation_summary(),
                                                    refresh_completion_state(),
                                                )
                                            )
                                            subcategory_input.on_value_change(
                                                lambda _, fn=update_split_subcategory_project: (
                                                    fn(),
                                                    refresh_allocation_summary(),
                                                    refresh_completion_state(),
                                                )
                                            )
                                            project_input.on_value_change(
                                                lambda _, fn=update_split_subcategory_project: (
                                                    fn(),
                                                    refresh_allocation_summary(),
                                                    refresh_completion_state(),
                                                )
                                            )
                                            amount_input.on(
                                                "update:model-value",
                                                lambda e, fn=update_split_amount, inp=amount_input: (
                                                    fn(_extract_model_value(e, inp.value)),
                                                    refresh_allocation_summary(),
                                                    refresh_completion_state(),
                                                ),
                                            )
                                            amount_input.on(
                                                "keydown.enter",
                                                lambda _, fn=update_split_amount, inp=amount_input: (
                                                    fn(inp.value),
                                                    refresh_allocation_summary(),
                                                    refresh_completion_state(),
                                                ),
                                            )

                                    ui.button("Zeile hinzufügen", icon="add", on_click=lambda: add_allocation_row()).props("flat")

                        def add_allocation_row() -> None:
                            gross_cents: int | None
                            try:
                                gross_cents = _parse_money_to_cents(gross_input.value, allow_negative=True)
                            except ValueError:
                                gross_cents = None
                            _, diff = _allocation_total_and_diff_cents(
                                gross_cents,
                                [row.get("amount_text") for row in allocation_rows],
                                allow_negative=True,
                            )
                            should_prefill_remaining = (
                                diff is not None
                                and gross_cents is not None
                                and ((gross_cents >= 0 and diff > 0) or (gross_cents < 0 and diff < 0))
                            )
                            next_amount_text = cents_to_text(diff) if should_prefill_remaining else ""
                            previous_row = allocation_rows[-1] if allocation_rows else {}
                            new_row = {
                                "cost_type_id": previous_row.get("cost_type_id"),
                                "cost_subcategory_id": previous_row.get("cost_subcategory_id"),
                                "project_id": previous_row.get("project_id"),
                                "amount_text": next_amount_text,
                            }
                            ensure_subcategory_for_row(new_row)
                            allocation_rows.append(new_row)
                            render_allocation_editor()
                            refresh_allocation_summary()
                            refresh_completion_state()

                        def remove_allocation_row(index: int) -> None:
                            nonlocal allocation_rows
                            if len(allocation_rows) <= 1:
                                return
                            allocation_rows = [row for idx, row in enumerate(allocation_rows) if idx != index]
                            render_allocation_editor()
                            refresh_allocation_summary()
                            refresh_completion_state()

                        apply_document_type_sign_to_inputs(flip_existing=False)
                        render_allocation_editor()
                        refresh_allocation_summary()
                        refresh_completion_state()

                        if is_deleted:
                            date_input.disable()
                            supplier_input.disable()
                            supplier_add_btn.disable()
                            document_type_input.disable()
                            gross_input.disable()
                            vat_input.disable()
                            notes_input.disable()

                        mark_clean()

                        async def _detail_move_to_deleted(receipt_id_for_action: int | None) -> None:
                            if not receipt_id_for_action:
                                return
                            try:
                                services.receipt_service.move_to_trash(receipt_id_for_action)
                                _queue_flash_notification("Beleg in Gelöschte Belege verschoben", type="positive")
                                await clean_and_navigate(receipt_return_path)
                            except Exception as exc:
                                _notify_error("Löschen fehlgeschlagen", exc)

                        async def _detail_restore(receipt_id_for_action: int | None) -> None:
                            if not receipt_id_for_action:
                                return
                            try:
                                services.receipt_service.restore_from_trash(receipt_id_for_action)
                                _queue_flash_notification("Beleg wiederhergestellt", type="positive")
                                await clean_and_navigate(receipt_return_path)
                            except Exception as exc:
                                _notify_error("Wiederherstellung fehlgeschlagen", exc)

                        async def _detail_save() -> None:
                            await _flush_active_input(client)
                            try:
                                result = services.receipt_service.save_detail(
                                    rid,
                                    build_receipt_save_input(strict=True),
                                )
                            except Exception as exc:
                                _notify_error("Speichern fehlgeschlagen", exc)
                                return

                            _queue_flash_notification(
                                "Beleg vollständig gespeichert"
                                if result.is_complete
                                else "Beleg gespeichert. Pflichtangaben fehlen noch.",
                                type="positive" if result.is_complete else "warning",
                            )
                            await clean_and_navigate(receipt_return_path)

    @ui.page("/verkaeufe")
    def orders_page() -> None:
        with _shell("/verkaeufe", "Verkäufe"):
            with ui.card().classes("bm-card p-4 w-full"):
                view_mode = "active"
                filters_visible = False
                search_task: asyncio.Task | None = None

                with ui.row().classes("w-full items-center justify-between gap-3 wrap"):
                    with ui.row().classes("gap-2 wrap"):
                        active_view_button = ui.button("Verkäufe").props("unelevated no-caps").classes(
                            "bm-view-mode-btn bm-segment-btn"
                        )
                        deleted_view_button = ui.button("Gelöschte Verkäufe").props("unelevated no-caps").classes(
                            "bm-view-mode-btn bm-segment-btn"
                        )
                    with ui.row().classes("gap-2 wrap"):
                        filter_toggle_button = ui.button("Filter", icon="filter_alt").props("flat no-caps").classes(
                            "bm-filter-btn"
                        )
                        create_button = ui.button("Verkauf anlegen", icon="add").props(
                            "color=primary unelevated no-caps"
                        ).classes("bm-filter-btn bm-toolbar-btn")

                contact_hint = ui.row().classes("w-full items-center gap-2 hidden")
                with contact_hint:
                    ui.icon("info")
                    ui.label("Für neue Verkäufe brauchst du zuerst mindestens einen Kontakt.")
                    ui.button("Zu Kontakten", on_click=lambda: ui.navigate.to("/kontakte")).props("flat color=primary")

                filter_row = ui.row().classes("w-full bm-filter-row hidden")
                with filter_row:
                    query_input = ui.input("Suche").props("clearable").classes("min-w-72 bm-filter-field")
                    contact_select = ui.select(
                        {},
                        label="Kontakte",
                        multiple=True,
                        with_input=True,
                        clearable=True,
                    ).classes("min-w-56 w-56 bm-filter-field")
                    contact_select.props("use-chips")
                    project_select = ui.select(
                        {},
                        label="Projekte",
                        multiple=True,
                        with_input=True,
                        clearable=True,
                    ).classes("min-w-56 w-56 bm-filter-field")
                    project_select.props("use-chips")
                    status_select = ui.select(
                        {
                            "draft": "Entwurf",
                            "document_missing": "Dokument fehlt",
                            "invoiced": "Abgerechnet",
                        },
                        label="Status",
                        multiple=True,
                        clearable=True,
                    ).classes("min-w-56 w-56 bm-filter-field")
                    status_select.props("use-chips")
                    date_from_input = ui.input("Verkaufsdatum von").props("type=date clearable").classes(
                        "w-44 bm-filter-field"
                    )
                    date_to_input = ui.input("Verkaufsdatum bis").props("type=date clearable").classes(
                        "w-44 bm-filter-field"
                    )
                    ui.button("Filter löschen", icon="close", on_click=lambda: clear_filters()).props("flat").classes(
                        "bm-filter-btn"
                    )

                results_column = ui.column().classes("w-full gap-3")

                def open_detail_page(order_id: int) -> None:
                    if order_id <= 0:
                        return
                    ui.navigate.to(f"/verkaeufe/{order_id}")

                def apply_view_button_styles() -> None:
                    active_view_button.classes(remove="bm-segment-btn--active bm-segment-btn--inactive")
                    deleted_view_button.classes(remove="bm-segment-btn--active bm-segment-btn--inactive")
                    if view_mode == "active":
                        active_view_button.classes(add="bm-segment-btn--active")
                        deleted_view_button.classes(add="bm-segment-btn--inactive")
                    else:
                        active_view_button.classes(add="bm-segment-btn--inactive")
                        deleted_view_button.classes(add="bm-segment-btn--active")

                def set_view_mode(next_mode: str) -> None:
                    nonlocal view_mode
                    if next_mode == view_mode:
                        return
                    view_mode = next_mode
                    apply_view_button_styles()
                    render_results()

                def schedule_render(delay_seconds: float = 0.25) -> None:
                    nonlocal search_task
                    if search_task and not search_task.done():
                        search_task.cancel()

                    async def delayed() -> None:
                        try:
                            await asyncio.sleep(delay_seconds)
                        except asyncio.CancelledError:
                            return
                        render_results()

                    search_task = asyncio.create_task(delayed())

                def clear_filters() -> None:
                    query_input.value = ""
                    contact_select.value = []
                    project_select.value = []
                    status_select.value = []
                    date_from_input.value = ""
                    date_to_input.value = ""
                    render_results()

                def apply_filter_visibility() -> None:
                    filter_row.classes(remove="hidden")
                    filter_toggle_button.classes(remove="bm-segment-btn--active bm-segment-btn--inactive")
                    if filters_visible:
                        filter_toggle_button.classes(add="bm-segment-btn bm-segment-btn--active")
                    else:
                        filter_row.classes(add="hidden")
                        filter_toggle_button.classes(add="bm-segment-btn bm-segment-btn--inactive")

                def toggle_filters() -> None:
                    nonlocal filters_visible
                    filters_visible = not filters_visible
                    apply_filter_visibility()

                def refresh_filter_options() -> None:
                    contact_map = contact_options()
                    project_map = project_options(active_only=False)
                    current_contacts = [item for item in _to_int_list(contact_select.value) if item in contact_map]
                    current_projects = [item for item in _to_int_list(project_select.value) if item in project_map]
                    contact_select.set_options(contact_map, value=current_contacts)
                    project_select.set_options(project_map, value=current_projects)
                    has_contacts = bool(contact_map)
                    contact_hint.classes(remove="hidden")
                    if has_contacts:
                        contact_hint.classes(add="hidden")
                        create_button.enable()
                    else:
                        create_button.disable()
                        if view_mode == "deleted":
                            contact_hint.classes(add="hidden")

                def open_create_order_dialog() -> None:
                    contact_map = contact_options()
                    if not contact_map:
                        ui.notify("Bitte zuerst mindestens einen Kontakt anlegen", type="warning")
                        ui.navigate.to("/kontakte")
                        return

                    category_options_map = contact_category_options()
                    if not category_options_map:
                        ui.notify("Bitte zuerst mindestens eine Kontaktkategorie anlegen", type="warning")
                        ui.navigate.to("/kontakte")
                        return
                    default_category_id = next(
                        (
                            category_id
                            for category_id, label in category_options_map.items()
                            if label == DEFAULT_CONTACT_CATEGORY_NAME
                        ),
                        next(iter(category_options_map)),
                    )

                    with ui.dialog() as dialog, ui.card().classes("p-4 w-[560px] max-w-full"):
                        ui.label("Neuer Verkauf").classes("text-lg font-semibold")
                        with ui.row().classes("w-full gap-3 wrap items-center"):
                            contact_input = ui.select(
                                contact_map,
                                value=next(iter(contact_map)),
                                label="Kontakt",
                            ).props("use-input input-debounce=0").classes("min-w-0 flex-1")
                            contact_add_btn = ui.button(
                                icon="add",
                                on_click=lambda: open_quick_contact_dialog(),
                            ).props("flat").classes("bm-inline-create-btn")
                            contact_add_btn.tooltip("Neuen Kontakt anlegen")
                        sale_date_input = ui.input("Verkaufsdatum", value=date.today().isoformat()).props(
                            "type=date"
                        ).classes("w-full")

                        def reload_contact_options(selected_id: int | None = None) -> None:
                            nonlocal contact_map
                            contact_map = contact_options()
                            current = contact_input.value if isinstance(contact_input.value, int) else None
                            next_value = None
                            if isinstance(selected_id, int) and selected_id in contact_map:
                                next_value = selected_id
                            elif isinstance(current, int) and current in contact_map:
                                next_value = current
                            elif contact_map:
                                next_value = next(iter(contact_map))
                            contact_input.set_options(contact_map, value=next_value)

                        def open_quick_contact_dialog() -> None:
                            with ui.dialog() as contact_dialog, ui.card().classes("p-4 w-[760px] max-w-full"):
                                ui.label("Neuer Kontakt").classes("text-lg font-semibold")
                                contact_fields = build_contact_inputs(
                                    include_category=False,
                                    include_extended_fields=True,
                                    include_notes=False,
                                )

                                def save_contact() -> None:
                                    try:
                                        payload = contact_form_values(contact_fields)
                                        contact = masterdata.create_contact(
                                            contact_category_id=default_category_id,
                                            **payload,
                                        )
                                        contact_id = contact.id
                                        ui.notify("Kontakt angelegt", type="positive")
                                        reload_contact_options(selected_id=contact_id if isinstance(contact_id, int) else None)
                                        contact_dialog.close()
                                    except Exception as exc:
                                        _notify_error("Kontakt konnte nicht angelegt werden", exc)

                                with ui.row().classes("w-full justify-end gap-2"):
                                    ui.button("Abbrechen", on_click=contact_dialog.close).props("flat")
                                    ui.button("Anlegen", on_click=save_contact).props("color=primary")

                            contact_dialog.open()

                        def create_order() -> None:
                            contact_id = contact_input.value
                            if not isinstance(contact_id, int):
                                ui.notify("Kontakt fehlt", type="negative")
                                return
                            try:
                                order = services.order_service.create_order(
                                    contact_id=contact_id,
                                    sale_date=_parse_iso_date(sale_date_input.value),
                                )
                            except Exception as exc:
                                _notify_error("Verkauf konnte nicht angelegt werden", exc)
                                return
                            dialog.close()
                            ui.navigate.to(f"/verkaeufe/{order.id}")

                        with ui.row().classes("w-full justify-end gap-2"):
                            ui.button("Abbrechen", on_click=dialog.close).props("flat")
                            ui.button("Anlegen", on_click=create_order).props("color=primary")
                    dialog.open()

                def hard_delete_order(order_id: int, rerender: Callable[[], None]) -> None:
                    with ui.dialog() as dialog, ui.card().classes("p-4 max-w-lg"):
                        ui.label("Verkauf endgültig löschen?").classes("text-lg font-semibold")
                        ui.label("Positionen und Kopfdaten werden dauerhaft entfernt.")
                        with ui.row().classes("w-full justify-end gap-2"):
                            ui.button("Abbrechen", on_click=dialog.close).props("flat")

                            def execute_delete() -> None:
                                try:
                                    services.order_service.hard_delete(order_id)
                                    ui.notify("Verkauf endgültig gelöscht", type="positive")
                                except Exception as exc:
                                    _notify_error("Endgültiges Löschen fehlgeschlagen", exc)
                                    return
                                dialog.close()
                                rerender()

                            ui.button("Endgültig löschen", on_click=execute_delete).props("color=negative")
                    dialog.open()

                def move_to_deleted(order_id: int) -> None:
                    if order_id <= 0:
                        return
                    try:
                        services.order_service.move_to_trash(order_id)
                        ui.notify("Verkauf in Gelöschte Verkäufe verschoben", type="positive")
                    except Exception as exc:
                        _notify_error("Löschen fehlgeschlagen", exc)
                        return
                    render_results()

                def restore_order(order_id: int) -> None:
                    if order_id <= 0:
                        return
                    try:
                        services.order_service.restore_from_trash(order_id)
                        ui.notify("Verkauf wiederhergestellt", type="positive")
                    except Exception as exc:
                        _notify_error("Wiederherstellung fehlgeschlagen", exc)
                        return
                    render_results()

                def render_results() -> None:
                    refresh_filter_options()
                    results_column.clear()
                    deleted_view = view_mode == "deleted"
                    orders = services.order_search_service.search(
                        query=(query_input.value or "").strip(),
                        contact_ids=_to_int_list(contact_select.value),
                        project_ids=_to_int_list(project_select.value),
                        statuses=[str(item) for item in (status_select.value or [])],
                        date_from=_parse_iso_date(date_from_input.value),
                        date_to=_parse_iso_date(date_to_input.value),
                        deleted_only=deleted_view,
                    )

                    with results_column:
                        if not orders:
                            label = (
                                "Keine gelöschten Verkäufe gefunden."
                                if deleted_view
                                else "Keine Verkäufe für die aktuellen Filter gefunden."
                            )
                            with ui.card().classes("bm-card p-4"):
                                ui.label(label)
                            return

                        rows: list[dict[str, Any]] = []
                        for order in orders:
                            status_key = order_status_key(order)
                            locked = (
                                order.invoice_date is not None
                                or bool((order.invoice_number or "").strip())
                                or bool((order.invoice_document_path or "").strip())
                            )
                            rows.append(
                                {
                                    "id": order.id,
                                    "internal_number": order.internal_number,
                                    "invoice_number": order.invoice_number or "-",
                                    "contact": _contact_display_name(order.contact) if order.contact else "-",
                                    "sale_date": order.sale_date.isoformat(),
                                    "invoice_date": order.invoice_date.isoformat() if order.invoice_date else "-",
                                    "status": order_status_label(order),
                                    "status_color": {
                                        "draft": "grey-6",
                                        "document_missing": "warning",
                                        "invoiced": "positive",
                                    }[status_key],
                                    "total": _format_cents(order_total_cents(order.items), settings.default_currency),
                                    "mobile_title": order.internal_number,
                                    "mobile_title_note": (
                                        f"Rechnungsnummer {order.invoice_number}" if (order.invoice_number or "").strip() else ""
                                    ),
                                    "mobile_primary_left": f"Verkaufsdatum {order.sale_date.isoformat()}",
                                    "mobile_primary_right": _format_cents(order_total_cents(order.items), settings.default_currency),
                                    "mobile_secondary": _contact_display_name(order.contact) if order.contact else "Kein Kontakt hinterlegt",
                                    "mobile_badge": order_status_label(order),
                                    "mobile_badge_color": {
                                        "draft": "grey-6",
                                        "document_missing": "warning",
                                        "invoiced": "positive",
                                    }[status_key],
                                    "deleted": bool(order.deleted_at),
                                    "locked": locked,
                                }
                            )

                        columns = [
                            {
                                "name": "internal_number",
                                "label": "Verkaufsnummer",
                                "field": "internal_number",
                                "align": "left",
                                "sortable": True,
                            },
                            {"name": "invoice_number", "label": "Rechnungsnummer", "field": "invoice_number", "align": "left"},
                            {"name": "contact", "label": "Kontakt", "field": "contact", "align": "left", "sortable": True},
                            {"name": "sale_date", "label": "Verkaufsdatum", "field": "sale_date", "align": "left", "sortable": True},
                            {"name": "invoice_date", "label": "Rechnungsdatum", "field": "invoice_date", "align": "left", "sortable": True},
                            {"name": "status", "label": "Status", "field": "status", "align": "left"},
                            {"name": "total", "label": f"Gesamt ({settings.default_currency})", "field": "total", "align": "right"},
                            {"name": "actions", "label": "Aktionen", "field": "actions", "align": "right"},
                        ]
                        actions_menu = """
                        <q-btn flat round dense icon="more_vert" @click.stop>
                          <q-menu auto-close>
                            <q-list dense style="min-width: 220px">
                              <q-item clickable @click="$parent.$emit('detail_action', props.row)">
                                <q-item-section avatar><q-icon name="visibility" /></q-item-section>
                                <q-item-section><q-item-label>Details anzeigen</q-item-label></q-item-section>
                              </q-item>
                              <q-item v-if="!props.row.deleted && !props.row.locked" clickable @click="$parent.$emit('delete_action', props.row)">
                                <q-item-section avatar><q-icon name="delete_outline" color="negative" /></q-item-section>
                                <q-item-section><q-item-label>In Gelöschte Verkäufe</q-item-label></q-item-section>
                              </q-item>
                              <q-item v-if="props.row.deleted" clickable @click="$parent.$emit('restore_action', props.row)">
                                <q-item-section avatar><q-icon name="restore_from_trash" /></q-item-section>
                                <q-item-section><q-item-label>Wiederherstellen</q-item-label></q-item-section>
                              </q-item>
                              <q-item v-if="props.row.deleted" clickable @click="$parent.$emit('hard_delete_action', props.row)">
                                <q-item-section avatar><q-icon name="delete_forever" color="negative" /></q-item-section>
                                <q-item-section><q-item-label>Endgültig löschen</q-item-label></q-item-section>
                              </q-item>
                            </q-list>
                          </q-menu>
                        </q-btn>
                        """
                        table = _responsive_erp_table(
                            columns=columns,
                            rows=rows,
                            row_key="id",
                            rows_per_page=25,
                            mobile_actions_slot=actions_menu,
                        )
                        table.add_slot(
                            "body-cell-status",
                            """
                            <q-td :props="props">
                              <q-badge :color="props.row.status_color">
                                {{ props.row.status }}
                              </q-badge>
                            </q-td>
                            """,
                        )
                        table.add_slot(
                            "body-cell-actions",
                            f"""
                            <q-td :props="props" class="text-right">
                              {actions_menu}
                            </q-td>
                            """,
                        )
                        table.on("detail_action", lambda e: open_detail_page(_extract_row_id(e) or -1))
                        table.on("delete_action", lambda e: move_to_deleted(_extract_row_id(e) or -1))
                        table.on("restore_action", lambda e: restore_order(_extract_row_id(e) or -1))
                        table.on(
                            "hard_delete_action",
                            lambda e: hard_delete_order(_extract_row_id(e) or -1, render_results),
                        )
                        table.on("rowClick", lambda e: open_detail_page(_extract_row_id(e) or -1))

                query_input.on("update:model-value", lambda _: schedule_render(0.35))
                query_input.on("keydown.enter", lambda _: render_results())
                contact_select.on_value_change(lambda _: schedule_render(0.05))
                project_select.on_value_change(lambda _: schedule_render(0.05))
                status_select.on_value_change(lambda _: schedule_render(0.05))
                date_from_input.on("update:model-value", lambda _: schedule_render(0.05))
                date_to_input.on("update:model-value", lambda _: schedule_render(0.05))

                active_view_button.on("click", lambda _: set_view_mode("active"))
                deleted_view_button.on("click", lambda _: set_view_mode("deleted"))
                filter_toggle_button.on("click", lambda _: toggle_filters())
                create_button.on("click", lambda _: open_create_order_dialog())
                apply_view_button_styles()
                apply_filter_visibility()
                refresh_filter_options()
                render_results()

    @ui.page("/verkaeufe/{order_id}")
    def order_detail_page(order_id: str) -> None:
        try:
            oid = int(order_id)
        except ValueError:
            oid = -1

        mark_dirty, mark_clean, is_dirty, guarded_navigate, clean_and_navigate = create_dirty_guard(
            f"atelierBuddyOrderDirty_{oid}_{uuid.uuid4().hex}"
        )

        with _shell(
            "/verkaeufe",
            "Verkaufsdetail",
            show_page_head=False,
            navigate_to=guarded_navigate,
            rerender_path=f"/verkaeufe/{oid}",
        ):
            client = context.client
            if oid <= 0:
                with ui.card().classes("bm-card p-4 w-full"):
                    ui.label("Ungültige Verkaufs-ID")
                return

            with Session(engine) as session:
                order = session.exec(
                    select(Order)
                    .where(Order.id == oid)
                    .options(
                        selectinload(Order.contact),
                        selectinload(Order.items).selectinload(OrderItem.project),
                    )
                ).first()
                contacts = list(session.exec(select(Contact).order_by(Contact.family_name, Contact.given_name)).all())

                selected_project_ids: list[int] = []
                if order:
                    selected_project_ids = sorted({item.project_id for item in order.items if isinstance(item.project_id, int)})
                projects = list(
                    session.exec(
                        select(Project)
                        .where(or_(Project.active.is_(True), Project.id.in_(selected_project_ids)))
                        .order_by(Project.name)
                    ).all()
                )

            if not order:
                with ui.card().classes("bm-card p-4 w-full"):
                    ui.label("Verkauf nicht gefunden")
                return

            contact_map = {contact.id: _contact_display_name(contact) for contact in contacts if contact.id is not None}
            project_map = {project.id: project.name for project in projects if project.id is not None}
            is_deleted = order.deleted_at is not None
            is_locked_for_delete = (
                order.invoice_date is not None
                or bool((order.invoice_number or "").strip())
                or bool((order.invoice_document_path or "").strip())
            )
            document_state: dict[str, str | None] = {
                "path": order.invoice_document_path,
                "name": (order.invoice_document_original_filename or "").strip()
                or Path(order.invoice_document_path or "").name
                or None,
                "source": (order.invoice_document_source or "").strip() or None,
            }
            invoice_generation_state: dict[str, Any] = {
                "running": False,
                "stalled": False,
                "task_id": None,
                "started_at": None,
                "feedback": None,
            }

            def current_invoice_document_url() -> str | None:
                return to_files_url(document_state.get("path"))

            def current_invoice_document_name() -> str:
                return (document_state.get("name") or "").strip() or Path(document_state.get("path") or "").name or "Rechnungsdokument"

            def current_invoice_document_source_label() -> str | None:
                source = (document_state.get("source") or "").strip().lower()
                if source == "generated":
                    return "Automatisch generiert"
                if source == "uploaded":
                    return "Manuell hochgeladen"
                return None

            def invoice_fields_locked() -> bool:
                return bool(current_invoice_document_url())

            def current_generation_blockers() -> list[str]:
                if invoice_generation_state["running"]:
                    return []
                if is_deleted:
                    return ["Gelöschte Verkäufe können nicht fakturiert werden."]
                try:
                    issues = services.invoice_service.collect_generation_issues(oid)
                except Exception as exc:
                    return [str(exc)]
                return issues

            def show_generation_feedback() -> None:
                blockers = current_generation_blockers()
                if blockers:
                    document_hint_label.text = blockers[0]
                    _notify_client(client, blockers[0], type="warning")
                    return
                document_hint_label.text = "Rechnung kann erzeugt werden."

            def safe_invoice_ui_update(callback: Callable[[], None], *, label: str) -> None:
                try:
                    callback()
                except RuntimeError:
                    LOG.info("UI-Update '%s' wurde uebersprungen, weil die Seite nicht mehr aktiv ist.", label)

            def begin_invoice_generation() -> str:
                task_id = uuid.uuid4().hex
                invoice_generation_state["running"] = True
                invoice_generation_state["stalled"] = False
                invoice_generation_state["task_id"] = task_id
                invoice_generation_state["started_at"] = time.monotonic()
                invoice_generation_state["feedback"] = "Rechnungs-PDF wird erzeugt ..."
                document_hint_label.text = "Rechnungs-PDF wird erzeugt ..."
                render_invoice_document_controls()
                return task_id

            def mark_invoice_generation_stalled(task_id: str, message: str) -> None:
                if invoice_generation_state.get("task_id") != task_id or not invoice_generation_state["running"]:
                    return
                invoice_generation_state["stalled"] = True
                invoice_generation_state["feedback"] = message
                document_hint_label.text = message
                render_invoice_document_controls()

            def finish_invoice_generation(task_id: str) -> None:
                if invoice_generation_state.get("task_id") != task_id:
                    return
                invoice_generation_state["running"] = False
                invoice_generation_state["stalled"] = False
                invoice_generation_state["task_id"] = None
                invoice_generation_state["started_at"] = None

            def fail_invoice_generation(task_id: str, message: str, *, notify_type: str = "negative") -> None:
                if invoice_generation_state.get("task_id") != task_id:
                    return
                finish_invoice_generation(task_id)
                invoice_generation_state["feedback"] = message
                document_hint_label.text = message
                render_invoice_document_controls()
                _notify_client(client, message, type=notify_type)

            async def run_invoice_generation(task_id: str) -> None:
                try:
                    result = await asyncio.to_thread(services.invoice_service.generate_invoice_document, oid)
                except Exception as exc:
                    if invoice_generation_state.get("task_id") != task_id:
                        return
                    def apply_generation_error_state() -> None:
                        finish_invoice_generation(task_id)
                        invoice_generation_state["feedback"] = None
                        _notify_error_with_client(client, "Rechnung konnte nicht erzeugt werden", exc)
                        render_invoice_document_controls()

                    safe_invoice_ui_update(apply_generation_error_state, label="invoice-generation-error")
                    return

                if invoice_generation_state.get("task_id") != task_id:
                    return

                def apply_generation_success_state() -> None:
                    finish_invoice_generation(task_id)
                    invoice_generation_state["feedback"] = None
                    document_state["path"] = result.generated_document_path
                    document_state["name"] = Path(result.generated_document_path).name
                    document_state["source"] = "generated"
                    invoice_date_input.value = result.order.invoice_date.isoformat() if result.order.invoice_date else ""
                    invoice_number_input.value = result.order.invoice_number or ""
                    _notify_client(client, "Rechnungs-PDF erzeugt", type="positive")
                    apply_invoice_field_lock()
                    render_invoice_document_controls()
                    render_item_editor()
                    refresh_total_preview()
                    mark_clean()

                safe_invoice_ui_update(apply_generation_success_state, label="invoice-generation-success")

            def poll_invoice_generation() -> None:
                task_id = invoice_generation_state.get("task_id")
                started_at = invoice_generation_state.get("started_at")
                if not task_id or not invoice_generation_state["running"] or started_at is None:
                    return

                elapsed = time.monotonic() - float(started_at)
                if elapsed >= INVOICE_GENERATION_MAX_WAIT_SECONDS:
                    fail_invoice_generation(
                        task_id,
                        "PDF-Erzeugung hat zu lange gedauert und wurde abgebrochen. Bitte erneut versuchen.",
                    )
                    return

                if elapsed >= INVOICE_GENERATION_FEEDBACK_TIMEOUT_SECONDS and not invoice_generation_state["stalled"]:
                    mark_invoice_generation_stalled(
                        task_id,
                        "Die PDF-Erzeugung dauert länger als erwartet. Der Vorgang läuft noch.",
                    )

            item_rows: list[dict[str, Any]] = []
            if order.items:
                for item in sorted(order.items, key=lambda current: current.position):
                    item_rows.append(
                        {
                            "description": item.description,
                            "quantity_text": _format_quantity(item.quantity),
                            "unit_price_text": ""
                            if item.unit_price_cents is None
                            else f"{(Decimal(item.unit_price_cents) / Decimal('100')):.2f}".replace(".", ","),
                            "project_id": item.project_id,
                        }
                    )
            if not item_rows:
                item_rows.append({"description": "", "quantity_text": "1", "unit_price_text": "", "project_id": None})
            project_mode_enabled = _uses_position_project_mode(item_rows)
            async def handle_invoice_document_upload(event: events.MultiUploadEventArguments) -> None:
                if not event.files:
                    return
                file_upload = event.files[0]
                new_document_path: Path | None = None
                was_dirty = is_dirty()
                try:
                    new_document_path = await save_uploaded_order_invoice(file_upload, oid)
                    old_document_path = services.order_service.set_invoice_document(
                        order_id=oid,
                        document_path=str(new_document_path),
                        original_filename=file_upload.name,
                        source="uploaded",
                    )
                    if old_document_path and old_document_path != str(new_document_path):
                        safe_delete_file(old_document_path)
                    document_state["path"] = str(new_document_path)
                    document_state["name"] = file_upload.name
                    document_state["source"] = "uploaded"
                    invoice_generation_state["feedback"] = None
                    _notify_client(client, "Rechnungsdokument gespeichert", type="positive")
                    apply_invoice_field_lock()
                    render_invoice_document_controls()
                    render_item_editor()
                    refresh_total_preview()
                    if not was_dirty:
                        mark_clean()
                except Exception as exc:
                    if new_document_path is not None:
                        safe_delete_file(new_document_path)
                    _notify_error_with_client(client, "Rechnungsdokument konnte nicht gespeichert werden", exc)

            def open_invoice_document() -> None:
                invoice_document_url = current_invoice_document_url()
                if not invoice_document_url:
                    _notify_client(client, "Kein Rechnungsdokument vorhanden", type="warning")
                    return
                _run_client_javascript(client, f"window.open({json.dumps(invoice_document_url)}, '_blank', 'noopener')")

            def remove_invoice_document() -> None:
                was_dirty = is_dirty()
                try:
                    old_document_path = services.order_service.remove_invoice_document(oid)
                    safe_delete_file(old_document_path)
                    document_state["path"] = None
                    document_state["name"] = None
                    document_state["source"] = None
                    invoice_generation_state["feedback"] = None
                    _notify_client(client, "Rechnungsdokument entfernt", type="positive")
                    apply_invoice_field_lock()
                    render_invoice_document_controls()
                    render_item_editor()
                    refresh_total_preview()
                    if not was_dirty:
                        mark_clean()
                except Exception as exc:
                    _notify_error_with_client(client, "Rechnungsdokument konnte nicht entfernt werden", exc)

            def confirm_remove_invoice_document() -> None:
                with ui.dialog() as remove_dialog, ui.card().classes("p-4 w-[520px] max-w-full"):
                    ui.label("Rechnungsdokument entfernen?").classes("text-lg font-semibold")
                    ui.label("Das aktuell hinterlegte Rechnungsdokument wird unwiderruflich gelöscht.")
                    with ui.row().classes("w-full justify-end gap-2"):
                        ui.button("Abbrechen", on_click=remove_dialog.close).props("flat")
                        ui.button(
                            "Dokument entfernen",
                            icon="delete_outline",
                            on_click=lambda: (
                                remove_dialog.close(),
                            remove_invoice_document(),
                        ),
                    ).props("color=negative")
                remove_dialog.open()

            async def generate_invoice_document() -> None:
                if invoice_generation_state["running"]:
                    _notify_client(client, "Rechnungs-PDF wird bereits erzeugt ...", type="warning")
                    return
                await _flush_active_input(client)
                if is_dirty():
                    try:
                        persist_order_from_form()
                        mark_clean()
                    except Exception as exc:
                        _notify_error_with_client(client, "Verkauf konnte vor der PDF-Erzeugung nicht gespeichert werden", exc)
                        render_invoice_document_controls()
                        return
                blockers = current_generation_blockers()
                if blockers:
                    _notify_client(client, blockers[0], type="warning")
                    render_invoice_document_controls()
                    return
                task_id = begin_invoice_generation()
                _notify_client(client, "Rechnungs-PDF wird erzeugt ...", type="info")
                asyncio.create_task(run_invoice_generation(task_id))

            def confirm_generate_invoice_document() -> None:
                if current_invoice_document_url():
                    with ui.dialog() as replace_dialog, ui.card().classes("p-4 w-[520px] max-w-full"):
                        ui.label("Rechnungsdokument ersetzen?").classes("text-lg font-semibold")
                        ui.label("Das aktuell hinterlegte Dokument wird durch eine neu erzeugte PDF ersetzt.")
                        with ui.row().classes("w-full justify-end gap-2"):
                            ui.button("Abbrechen", on_click=replace_dialog.close).props("flat")
                            ui.button(
                                "PDF erzeugen",
                                icon="picture_as_pdf",
                                on_click=lambda: (
                                    replace_dialog.close(),
                                    asyncio.create_task(generate_invoice_document()),
                                ),
                            ).props("color=primary")
                    replace_dialog.open()
                    return
                asyncio.create_task(generate_invoice_document())

            category_options_map = contact_category_options()
            default_contact_category_id = next(
                (
                    category_id
                    for category_id, label in category_options_map.items()
                    if label == DEFAULT_CONTACT_CATEGORY_NAME
                ),
                next(iter(category_options_map), None),
            )

            def reload_contact_options(selected_id: int | None = None) -> None:
                nonlocal contact_map
                contact_map = contact_options()
                try:
                    current = contact_input.value if isinstance(contact_input.value, int) else None
                except NameError:
                    current = None
                next_value = None
                if isinstance(selected_id, int) and selected_id in contact_map:
                    next_value = selected_id
                elif isinstance(current, int) and current in contact_map:
                    next_value = current
                elif contact_map:
                    next_value = next(iter(contact_map))
                contact_input.set_options(contact_map, value=next_value)

            def open_quick_contact_dialog() -> None:
                if not isinstance(default_contact_category_id, int):
                    ui.notify("Bitte zuerst mindestens eine Kontaktkategorie anlegen", type="warning")
                    return
                with ui.dialog() as contact_dialog, ui.card().classes("p-4 w-[760px] max-w-full"):
                    ui.label("Neuer Kontakt").classes("text-lg font-semibold")
                    contact_fields = build_contact_inputs(
                        include_category=False,
                        include_extended_fields=True,
                        include_notes=False,
                    )

                    def save_contact() -> None:
                        try:
                            payload = contact_form_values(contact_fields)
                            contact = masterdata.create_contact(
                                contact_category_id=default_contact_category_id,
                                **payload,
                            )
                            contact_id = contact.id
                            ui.notify("Kontakt angelegt", type="positive")
                            reload_contact_options(selected_id=contact_id if isinstance(contact_id, int) else None)
                            mark_dirty()
                            contact_dialog.close()
                        except Exception as exc:
                            _notify_error("Kontakt konnte nicht angelegt werden", exc)

                    with ui.row().classes("w-full justify-end gap-2"):
                        ui.button("Abbrechen", on_click=contact_dialog.close).props("flat")
                        ui.button("Anlegen", on_click=save_contact).props("color=primary")

                contact_dialog.open()

            def open_selected_contact() -> None:
                contact_id = contact_input.value
                if not isinstance(contact_id, int):
                    _notify_client(client, "Bitte zuerst einen Kontakt auswählen.", type="warning")
                    return
                with Session(engine) as session:
                    current_contact = session.get(Contact, contact_id)
                if current_contact is None:
                    _notify_client(client, "Der ausgewählte Kontakt wurde nicht gefunden.", type="warning")
                    reload_contact_options()
                    return

                with ui.dialog() as contact_dialog, ui.card().classes("p-4 w-[760px] max-w-full"):
                    ui.label("Kontakt bearbeiten").classes("text-lg font-semibold")
                    contact_fields = build_contact_inputs(
                        current_contact=current_contact,
                        include_category=False,
                        include_extended_fields=True,
                        include_notes=False,
                    )

                    def save_contact() -> None:
                        try:
                            payload = contact_form_values(contact_fields)
                            masterdata.update_contact(
                                contact_id=current_contact.id or -1,
                                contact_category_id=current_contact.contact_category_id,
                                **payload,
                            )
                            ui.notify("Kontakt gespeichert", type="positive")
                            reload_contact_options(selected_id=current_contact.id if isinstance(current_contact.id, int) else None)
                            mark_dirty()
                            contact_dialog.close()
                        except Exception as exc:
                            _notify_error("Kontakt konnte nicht gespeichert werden", exc)

                    with ui.row().classes("w-full justify-end gap-2"):
                        ui.button("Abbrechen", on_click=contact_dialog.close).props("flat")
                        ui.button("Speichern", on_click=save_contact).props("color=primary")

                contact_dialog.open()

            with ui.card().classes("bm-card p-4 w-full gap-4"):
                with ui.row().classes("bm-detail-toolbar w-full items-center justify-between"):
                    with ui.row().classes("items-center gap-2"):
                        back_btn = ui.button(
                            icon="close",
                            on_click=lambda: guarded_navigate("/verkaeufe"),
                        ).props("flat round dense").classes("bm-icon-action-btn")
                        back_btn.tooltip("Zurück zu Verkäufen")
                    with ui.row().classes("items-center gap-2"):
                        if not is_deleted and not is_locked_for_delete:
                            delete_btn = ui.button(
                                icon="delete_outline",
                                on_click=lambda: asyncio.create_task(_detail_move_to_deleted()),
                            ).props("flat round dense").classes("bm-icon-action-btn bm-icon-action-btn--danger")
                            delete_btn.tooltip("In Gelöschte Verkäufe")
                        if not is_deleted:
                            save_btn = ui.button(
                                icon="save",
                                on_click=lambda: asyncio.create_task(_save_order()),
                            ).props("flat round dense").classes("bm-icon-action-btn bm-icon-action-btn--primary")
                            save_btn.tooltip("Speichern")
                        else:
                            restore_btn = ui.button(
                                icon="restore_from_trash",
                                on_click=lambda: asyncio.create_task(_detail_restore()),
                            ).props("flat round dense").classes("bm-icon-action-btn bm-icon-action-btn--success")
                            restore_btn.tooltip("Wiederherstellen")

                ui.label(f"Verkauf {order.internal_number}").classes("text-xl font-semibold")

                with ui.row().classes("w-full gap-3 wrap items-end bm-form-row"):
                    with ui.row().classes("min-w-[260px] flex-[1_1_320px] gap-3 items-center bm-form-row"):
                        contact_input = ui.select(contact_map, value=order.contact_id, label="Kontakt").props(
                            "use-input input-debounce=0"
                        ).classes("min-w-0 flex-1 bm-form-field")
                        contact_edit_btn = ui.button(
                            icon="edit",
                            on_click=open_selected_contact,
                        ).props("flat").classes("bm-inline-create-btn")
                        contact_edit_btn.tooltip("Aktuellen Kontakt im Pop-out öffnen")
                        contact_add_btn = ui.button(
                            icon="add",
                            on_click=open_quick_contact_dialog,
                        ).props("flat").classes("bm-inline-create-btn")
                        contact_add_btn.tooltip("Neuen Kontakt anlegen")
                    internal_number_input = ui.input("Interne Verkaufsnummer", value=order.internal_number).props(
                        "readonly"
                    ).classes("min-w-[190px] flex-1 bm-form-field")
                    sale_date_input = ui.input("Verkaufsdatum", value=order.sale_date.isoformat()).props("type=date").classes(
                        "w-44 bm-form-field"
                    )
                    status_preview = ui.input("Status", value=order_status_label(order)).props("readonly").classes(
                        "w-40 bm-form-field"
                    )

                with ui.row().classes("w-full gap-4 items-start wrap bm-form-row"):
                    with ui.column().classes("min-w-[320px] flex-[1_1_520px] gap-3 bm-responsive-panel"):
                        notes_input = ui.textarea("Notiz", value=order.notes or "").props("rows=4").classes(
                            "w-full bm-order-notes bm-form-field"
                        )
                        with ui.row().classes("w-full gap-3 wrap items-center bm-form-row"):
                            head_project_container = ui.row().classes("min-w-[260px] flex-1 bm-form-row")
                            with head_project_container:
                                head_project_input = ui.select(
                                    project_map,
                                    value=None,
                                    label="Projekt für alle Positionen",
                                    clearable=True,
                                ).classes("w-full bm-form-field")
                            with ui.row().classes("items-center"):
                                project_mode_input = ui.switch("Projekt je Position", value=project_mode_enabled).classes(
                                    "bm-inline-switch bm-order-project-toggle"
                                )
                                project_mode_input.props("color=primary")

                    with ui.card().classes("bm-invoice-section bm-card p-4 min-w-[320px] flex-[1_1_360px] gap-3 bm-responsive-panel"):
                        with ui.row().classes("w-full items-center justify-between gap-2"):
                            ui.label("Rechnung").classes("text-base font-semibold")
                            document_actions_container = ui.row().classes("items-center gap-1")

                        with ui.row().classes("w-full gap-3 wrap items-end bm-form-row"):
                            invoice_date_input = ui.input(
                                "Rechnungsdatum",
                                value=order.invoice_date.isoformat() if order.invoice_date else "",
                            ).props("type=date clearable").classes("w-44 bm-form-field")
                            invoice_number_input = ui.input(
                                "Rechnungsnummer",
                                value=order.invoice_number or "",
                            ).classes("min-w-[180px] flex-1 bm-form-field")
                        document_hint_label = ui.label("").classes("text-xs text-slate-600")

                item_editor = ui.column().classes("w-full gap-2")
                with ui.row().classes("w-full items-end justify-between gap-3 wrap bm-form-row"):
                    ui.label("Alle Preise ohne Umsatzsteuer eingeben (§19 UStG).").classes("text-xs text-slate-600")
                    total_preview = ui.input("Gesamtsumme", value="-").props("readonly").classes("w-48 bm-form-field")

                def render_invoice_document_controls() -> None:
                    invoice_document_url = current_invoice_document_url()
                    blockers = current_generation_blockers()
                    generation_running = bool(invoice_generation_state["running"])
                    generation_stalled = bool(invoice_generation_state["stalled"])
                    document_actions_container.clear()
                    with document_actions_container:
                        if not is_deleted:
                            generate_document_btn = ui.button(
                                icon="hourglass_top" if generation_running else "picture_as_pdf",
                                on_click=(
                                    lambda: _notify_client(client, "Die Rechnungs-PDF wird bereits erzeugt.", type="warning")
                                    if generation_running
                                    else confirm_generate_invoice_document()
                                ),
                            ).props("flat round dense").classes("bm-icon-action-btn bm-icon-action-btn--primary")
                            generate_document_btn.tooltip(
                                "PDF-Erzeugung läuft" if generation_running else "PDF erzeugen"
                            )
                            if generation_running:
                                generate_document_btn.disable()
                        if invoice_document_url:
                            view_document_btn = ui.button(
                                icon="visibility",
                                on_click=open_invoice_document,
                            ).props("flat round dense").classes("bm-icon-action-btn")
                            view_document_btn.tooltip("Dokument anzeigen")
                            if generation_running:
                                view_document_btn.disable()
                        if not is_deleted:
                            document_upload = ui.upload(
                                multiple=False,
                                auto_upload=True,
                                on_multi_upload=handle_invoice_document_upload,
                                label="",
                            ).classes("bm-hidden-upload")
                            document_upload.props("accept=.pdf,.jpg,.jpeg,.png,.heic,.heif")
                            upload_btn = ui.button(
                                icon="upload_file",
                                on_click=lambda: document_upload.run_method("pickFiles"),
                            ).props("flat round dense").classes("bm-icon-action-btn")
                            upload_btn.tooltip("Dokument hochladen oder ersetzen")
                            if generation_running:
                                upload_btn.disable()
                        if invoice_document_url and not is_deleted:
                            remove_document_btn = ui.button(
                                icon="delete_outline",
                                on_click=confirm_remove_invoice_document,
                            ).props("flat round dense").classes("bm-icon-action-btn bm-icon-action-btn--danger")
                            remove_document_btn.tooltip("Dokument entfernen")
                            if generation_running:
                                remove_document_btn.disable()

                    source_label = current_invoice_document_source_label()
                    if generation_running and generation_stalled:
                        document_hint_label.text = invoice_generation_state.get("feedback") or (
                            "Die PDF-Erzeugung dauert länger als erwartet. Der Vorgang läuft noch."
                        )
                    elif generation_running:
                        document_hint_label.text = invoice_generation_state.get("feedback") or "Rechnungs-PDF wird erzeugt ..."
                    elif invoice_generation_state.get("feedback"):
                        document_hint_label.text = str(invoice_generation_state["feedback"])
                    elif invoice_document_url:
                        if source_label:
                            document_hint_label.text = f"{source_label}: {current_invoice_document_name()}"
                        else:
                            document_hint_label.text = f"Hinterlegt: {current_invoice_document_name()}"
                    elif blockers:
                        document_hint_label.text = blockers[0]
                    else:
                        document_hint_label.text = "Noch kein Rechnungsdokument hinterlegt."

                def parse_row_total_cents(row: dict[str, Any]) -> int | None:
                    try:
                        quantity = _parse_quantity(row.get("quantity_text"))
                        unit_price_cents = _parse_money_to_cents(row.get("unit_price_text"), allow_negative=True)
                    except ValueError:
                        return None
                    if quantity is None or unit_price_cents is None:
                        return None
                    return order_item_total_cents(quantity, unit_price_cents)

                def refresh_total_preview() -> None:
                    payload: list[OrderItemInput] = []
                    for index, row in enumerate(item_rows, start=1):
                        description = (row.get("description") or "").strip()
                        if not description and not (row.get("quantity_text") or "").strip() and not (row.get("unit_price_text") or "").strip():
                            continue
                        try:
                            quantity = _parse_quantity(row.get("quantity_text"))
                            unit_price_cents = _parse_money_to_cents(row.get("unit_price_text"), allow_negative=True)
                        except ValueError:
                            total_preview.value = "Ungültige Position"
                            status_preview.value = current_status_label()
                            return
                        if quantity is None or unit_price_cents is None:
                            total_preview.value = "Ungültige Position"
                            status_preview.value = current_status_label()
                            return
                        payload.append(
                            OrderItemInput(
                                description=description,
                                quantity=quantity,
                                unit_price_cents=unit_price_cents,
                                project_id=int(row.get("project_id")) if row.get("project_id") else None,
                                position=index,
                            )
                        )
                    total_preview.value = _format_cents(order_total_cents(payload), settings.default_currency) if payload else "-"
                    status_preview.value = current_status_label()
                    head_project_input.value = _common_project_id_from_rows(item_rows)

                def current_status_label() -> str:
                    has_invoice_number = bool((invoice_number_input.value or "").strip())
                    has_invoice_date = _parse_iso_date(invoice_date_input.value) is not None
                    has_invoice_document = bool(current_invoice_document_url())
                    if has_invoice_date and has_invoice_number and has_invoice_document:
                        return "Abgerechnet"
                    if (has_invoice_date or has_invoice_number) and not has_invoice_document:
                        return "Dokument fehlt"
                    return "Entwurf"

                def apply_project_mode_visibility() -> None:
                    if bool(project_mode_input.value):
                        head_project_input.disable()
                    else:
                        head_project_input.enable()

                def apply_contact_action_state() -> None:
                    if isinstance(contact_input.value, int):
                        contact_edit_btn.enable()
                    else:
                        contact_edit_btn.disable()

                def apply_invoice_field_lock() -> None:
                    locked = invoice_fields_locked() or is_deleted
                    if locked:
                        contact_input.disable()
                        contact_add_btn.disable()
                        sale_date_input.disable()
                        invoice_date_input.disable()
                        invoice_number_input.disable()
                        project_mode_input.disable()
                        head_project_input.disable()
                    else:
                        contact_input.enable()
                        contact_add_btn.enable()
                        sale_date_input.enable()
                        invoice_date_input.enable()
                        invoice_number_input.enable()
                        project_mode_input.enable()
                        apply_project_mode_visibility()
                    apply_contact_action_state()

                def render_item_row(row: dict[str, Any]) -> None:
                    locked = invoice_fields_locked() or is_deleted
                    with ui.card().classes("bm-card p-3 w-full"):
                        with ui.row().classes("w-full items-end gap-3 wrap bm-form-row"):
                            description_input = ui.input("Bezeichnung", value=row.get("description") or "").classes(
                                "min-w-[280px] grow bm-form-field"
                            )
                            quantity_input = ui.input("Menge", value=row.get("quantity_text") or "").classes("w-28 bm-form-field")
                            unit_price_input = ui.input(
                                f"Einzelpreis ({settings.default_currency})",
                                value=row.get("unit_price_text") or "",
                            ).classes("w-40 bm-form-field")
                            total_label = ui.input(
                                "Gesamt",
                                value="-",
                            ).props("readonly").classes("w-40 bm-form-field")
                            project_input = ui.select(
                                project_map,
                                value=row.get("project_id"),
                                label="Projekt",
                                clearable=True,
                            ).classes("min-w-[220px] grow bm-form-field")
                            remove_button = ui.button(
                                icon="delete_outline",
                                on_click=lambda current=row: remove_row(current),
                            ).props("flat round color=negative")
                            remove_button.tooltip("Position entfernen")
                        if locked:
                            description_input.disable()
                            quantity_input.disable()
                            unit_price_input.disable()
                            project_input.disable()
                            remove_button.disable()

                        project_input.classes(remove="hidden")
                        if not bool(project_mode_input.value):
                            project_input.classes(add="hidden")

                        def update_total_label() -> None:
                            total_cents = parse_row_total_cents(row)
                            total_label.value = (
                                _format_cents(total_cents, settings.default_currency) if total_cents is not None else "-"
                            )

                        def on_description_change(value: Any) -> None:
                            mark_dirty()
                            row["description"] = str(value or "")
                            refresh_total_preview()

                        def on_quantity_change(value: Any) -> None:
                            mark_dirty()
                            row["quantity_text"] = str(value or "")
                            update_total_label()
                            refresh_total_preview()

                        def normalize_quantity_on_blur() -> None:
                            try:
                                quantity_input.value = _normalize_quantity_input(quantity_input.value)
                            except ValueError:
                                return
                            mark_dirty()
                            row["quantity_text"] = quantity_input.value or ""
                            update_total_label()
                            refresh_total_preview()

                        def on_unit_price_change(value: Any) -> None:
                            mark_dirty()
                            row["unit_price_text"] = str(value or "")
                            update_total_label()
                            refresh_total_preview()

                        def normalize_unit_price_on_blur() -> None:
                            try:
                                unit_price_input.value = _normalize_money_input(
                                    unit_price_input.value,
                                    allow_negative=True,
                                )
                            except ValueError:
                                return
                            mark_dirty()
                            row["unit_price_text"] = unit_price_input.value or ""
                            update_total_label()
                            refresh_total_preview()

                        def on_project_change(value: Any) -> None:
                            mark_dirty()
                            row["project_id"] = int(value) if value else None
                            refresh_total_preview()

                        description_input.on_value_change(lambda e: on_description_change(e.value))
                        quantity_input.on_value_change(lambda e: on_quantity_change(e.value))
                        quantity_input.on("blur", lambda _: normalize_quantity_on_blur())
                        unit_price_input.on_value_change(lambda e: on_unit_price_change(e.value))
                        unit_price_input.on("blur", lambda _: normalize_unit_price_on_blur())
                        project_input.on_value_change(lambda e: on_project_change(e.value))
                        update_total_label()

                def render_item_editor() -> None:
                    item_editor.clear()
                    with item_editor:
                        with ui.row().classes("w-full items-center justify-between"):
                            ui.label("Positionen").classes("text-sm font-semibold")
                            add_button = ui.button("Position hinzufügen", icon="add", on_click=lambda: add_row()).props("flat")
                            if invoice_fields_locked() or is_deleted:
                                add_button.disable()

                        for row in item_rows:
                            render_item_row(row)

                def add_row() -> None:
                    if invoice_fields_locked() or is_deleted:
                        return
                    mark_dirty()
                    item_rows.append({"description": "", "quantity_text": "1", "unit_price_text": "", "project_id": None})
                    render_item_editor()
                    refresh_total_preview()

                def remove_row(row: dict[str, Any]) -> None:
                    if invoice_fields_locked() or is_deleted:
                        return
                    mark_dirty()
                    if len(item_rows) == 1:
                        item_rows[0] = {"description": "", "quantity_text": "1", "unit_price_text": "", "project_id": None}
                    else:
                        item_rows.remove(row)
                    render_item_editor()
                    refresh_total_preview()

                def build_item_payload() -> list[OrderItemInput]:
                    payload: list[OrderItemInput] = []
                    for index, row in enumerate(item_rows, start=1):
                        description = (row.get("description") or "").strip()
                        quantity_text = str(row.get("quantity_text") or "").strip()
                        unit_price_text = str(row.get("unit_price_text") or "").strip()
                        project_value = row.get("project_id")
                        if not description and not quantity_text and not unit_price_text and not project_value:
                            continue
                        quantity = _parse_quantity(quantity_text)
                        unit_price_cents = _parse_money_to_cents(unit_price_text, allow_negative=True)
                        if quantity is None:
                            raise ValueError(f"Menge fehlt in Position {index}")
                        if unit_price_cents is None:
                            raise ValueError(f"Einzelpreis fehlt in Position {index}")
                        payload.append(
                            OrderItemInput(
                                description=description,
                                quantity=quantity,
                                unit_price_cents=unit_price_cents,
                                project_id=int(project_value) if project_value else None,
                                position=index,
                            )
                        )
                    return payload

                def persist_order_from_form() -> Order:
                    contact_id = contact_input.value
                    if not isinstance(contact_id, int):
                        raise ValueError("Kontakt fehlt")
                    return services.order_service.save_order(
                        order_id=oid,
                        contact_id=contact_id,
                        sale_date=_parse_iso_date(sale_date_input.value),
                        invoice_date=_parse_iso_date(invoice_date_input.value),
                        invoice_number=invoice_number_input.value,
                        notes=notes_input.value,
                        items=build_item_payload(),
                    )

                async def _save_order() -> None:
                    await _flush_active_input(client)
                    try:
                        persist_order_from_form()
                    except Exception as exc:
                        _notify_error("Verkauf konnte nicht gespeichert werden", exc)
                        return
                    ui.notify("Verkauf gespeichert", type="positive")
                    await clean_and_navigate("/verkaeufe")

                async def _detail_move_to_deleted() -> None:
                    try:
                        services.order_service.move_to_trash(oid)
                    except Exception as exc:
                        _notify_error("Löschen fehlgeschlagen", exc)
                        return
                    ui.notify("Verkauf in Gelöschte Verkäufe verschoben", type="positive")
                    await clean_and_navigate("/verkaeufe")

                async def _detail_restore() -> None:
                    try:
                        services.order_service.restore_from_trash(oid)
                    except Exception as exc:
                        _notify_error("Wiederherstellung fehlgeschlagen", exc)
                        return
                    ui.notify("Verkauf wiederhergestellt", type="positive")
                    await clean_and_navigate(f"/verkaeufe/{oid}")

                def apply_head_project() -> None:
                    if bool(project_mode_input.value) or invoice_fields_locked() or is_deleted:
                        return
                    mark_dirty()
                    selected_project_id = head_project_input.value
                    for row in item_rows:
                        row["project_id"] = int(selected_project_id) if isinstance(selected_project_id, int) else None
                    render_item_editor()
                    refresh_total_preview()

                def on_project_mode_change() -> None:
                    if invoice_fields_locked() or is_deleted:
                        apply_invoice_field_lock()
                        return
                    mark_dirty()
                    apply_project_mode_visibility()
                    render_item_editor()
                    refresh_total_preview()

                head_project_input.value = _common_project_id_from_rows(item_rows)
                head_project_input.on_value_change(lambda _: apply_head_project())
                project_mode_input.on_value_change(lambda _: on_project_mode_change())
                contact_input.on_value_change(lambda _: (mark_dirty(), apply_contact_action_state()))
                sale_date_input.on_value_change(lambda _: mark_dirty())
                notes_input.on_value_change(lambda _: mark_dirty())
                invoice_date_input.on_value_change(lambda _: (mark_dirty(), refresh_total_preview()))
                invoice_number_input.on_value_change(lambda _: (mark_dirty(), refresh_total_preview()))
                apply_contact_action_state()
                apply_invoice_field_lock()
                render_invoice_document_controls()
                render_item_editor()
                refresh_total_preview()
                ui.timer(1.0, poll_invoice_generation)
                mark_clean()

    @ui.page("/projekte")
    def projects_page() -> None:
        with _shell("/projekte", "Projekte"):
            with ui.card().classes("bm-card p-4 w-full"):
                def open_create_project_dialog() -> None:
                    with ui.dialog() as dialog, ui.card().classes("p-4 w-[560px] max-w-full"):
                        ui.label("Neues Projekt").classes("text-lg font-semibold")
                        name_input = ui.input("Projektname").classes("w-full")
                        price_input = ui.input(f"Preis ({settings.default_currency}, optional)").classes("w-full")
                        created_on_input = ui.input("Erschaffen am (optional)").props("type=date clearable").classes("w-full")
                        active_input = ui.checkbox("Aktiv", value=True)

                        def add_project() -> None:
                            name = (name_input.value or "").strip()
                            if not name:
                                ui.notify("Projektname fehlt", type="negative")
                                return
                            try:
                                _, created = masterdata.create_or_update_project(
                                    name=name,
                                    active=bool(active_input.value),
                                    price_cents=_parse_money_to_cents(price_input.value),
                                    created_on=_parse_iso_date(created_on_input.value),
                                )
                                ui.notify(
                                    "Projekt angelegt" if created else "Projekt existierte bereits und wurde aktualisiert",
                                    type="positive",
                                )
                                dialog.close()
                                render_projects()
                            except Exception as exc:
                                _notify_error("Projekt konnte nicht angelegt werden", exc)

                        with ui.row().classes("w-full justify-end gap-2"):
                            ui.button("Abbrechen", on_click=dialog.close).props("flat")
                            ui.button("Anlegen", on_click=add_project).props("color=primary")
                    dialog.open()

                with ui.row().classes("w-full justify-end"):
                    ui.button("Projekt anlegen", icon="add", on_click=open_create_project_dialog).props("color=primary")

                project_column = ui.column().classes("w-full gap-2")

                def delete_project(project_id: int) -> None:
                    try:
                        old_cover_path = masterdata.delete_project(project_id=project_id)
                        safe_delete_file(old_cover_path)
                        ui.notify("Projekt entfernt", type="positive")
                        render_projects()
                    except Exception as exc:
                        _notify_error("Projekt konnte nicht gelöscht werden", exc)

                def open_cover_dialog(project_id: int) -> None:
                    with ui.dialog() as dialog, ui.card().classes("p-4 w-[520px] max-w-full"):
                        ui.label("Projekt-Cover setzen").classes("text-lg font-semibold")
                        ui.label("Beim Upload wird das Bild als optimiertes WebP gespeichert.").classes("text-sm text-slate-600")

                        async def handle_cover_upload(event: events.MultiUploadEventArguments) -> None:
                            if not event.files:
                                return
                            file_upload = event.files[0]
                            try:
                                new_cover_path = await save_uploaded_work_cover(file_upload, project_id)
                                old_cover_path = masterdata.set_project_cover(
                                    project_id=project_id,
                                    cover_path=str(new_cover_path),
                                )
                                if old_cover_path and old_cover_path != str(new_cover_path):
                                    safe_delete_file(old_cover_path)
                                ui.notify("Projekt-Cover gespeichert", type="positive")
                                dialog.close()
                                render_projects()
                            except Exception as exc:
                                _notify_error("Cover konnte nicht gespeichert werden", exc)

                        upload = ui.upload(
                            multiple=False,
                            auto_upload=True,
                            on_multi_upload=handle_cover_upload,
                            label="Bild hierher ziehen oder auswählen",
                        ).classes("w-full")
                        upload.props("accept=.jpg,.jpeg,.png,.heic,.heif")

                        with ui.row().classes("w-full justify-end"):
                            ui.button("Schließen", on_click=dialog.close).props("flat")
                    dialog.open()

                def open_project_detail(project_id: int) -> None:
                    if project_id <= 0:
                        return
                    ui.navigate.to(f"/projekte/{project_id}")

                def render_projects() -> None:
                    with Session(engine) as session:
                        projects = list(session.exec(select(Project).order_by(Project.name)).all())

                    project_column.clear()
                    with project_column:
                        if not projects:
                            ui.label("Noch keine Projekte vorhanden.")
                            return

                        rows = [
                            {
                                "id": project.id,
                                "cover_url": to_files_url(project.cover_image_path),
                                "name": project.name,
                                "price": _format_cents(project.price_cents, settings.default_currency),
                                "created_on": project.created_on.isoformat() if project.created_on else "-",
                                "status": "aktiv" if project.active else "inaktiv",
                                "mobile_title": project.name,
                                "mobile_title_note": "Cover hinterlegt" if project.cover_image_path else "Kein Cover",
                                "mobile_primary_left": (
                                    f"Erschaffen am {project.created_on.isoformat()}" if project.created_on else "Erschaffen am -"
                                ),
                                "mobile_primary_right": _format_cents(project.price_cents, settings.default_currency),
                                "mobile_secondary": "aktiv" if project.active else "inaktiv",
                                "mobile_badge": "aktiv" if project.active else "inaktiv",
                                "mobile_badge_color": "positive" if project.active else "grey-7",
                            }
                            for project in projects
                            if project.id is not None
                        ]
                        columns = [
                            {"name": "cover", "label": "Cover", "field": "cover", "align": "left"},
                            {"name": "name", "label": "Projekt", "field": "name", "align": "left", "sortable": True},
                            {
                                "name": "price",
                                "label": f"Preis ({settings.default_currency})",
                                "field": "price",
                                "align": "right",
                                "sortable": True,
                            },
                            {
                                "name": "created_on",
                                "label": "Erschaffen am",
                                "field": "created_on",
                                "align": "left",
                                "sortable": True,
                            },
                            {"name": "status", "label": "Status", "field": "status", "align": "left"},
                            {"name": "actions", "label": "Aktionen", "field": "actions", "align": "right"},
                        ]
                        actions_menu = """
                        <q-btn flat round dense icon="more_vert" @click.stop>
                          <q-menu auto-close>
                            <q-list dense style="min-width: 220px">
                              <q-item clickable @click="$parent.$emit('detail_action', props.row)">
                                <q-item-section avatar><q-icon name="visibility" /></q-item-section>
                                <q-item-section><q-item-label>Details anzeigen</q-item-label></q-item-section>
                              </q-item>
                              <q-item clickable @click="$parent.$emit('cover_action', props.row)">
                                <q-item-section avatar><q-icon name="image" /></q-item-section>
                                <q-item-section><q-item-label>Cover setzen</q-item-label></q-item-section>
                              </q-item>
                              <q-item clickable @click="$parent.$emit('delete_action', props.row)">
                                <q-item-section avatar><q-icon name="delete" color="negative" /></q-item-section>
                                <q-item-section><q-item-label>Projekt löschen</q-item-label></q-item-section>
                              </q-item>
                            </q-list>
                          </q-menu>
                        </q-btn>
                        """
                        table = _responsive_erp_table(
                            columns=columns,
                            rows=rows,
                            row_key="id",
                            rows_per_page=20,
                            mobile_actions_slot=actions_menu,
                        )
                        table.add_slot(
                            "body-cell-cover",
                            """
                            <q-td :props="props">
                              <img v-if="props.row.cover_url" :src="props.row.cover_url"
                                   style="width:44px;height:44px;border-radius:8px;object-fit:cover;" />
                              <q-icon v-else name="image" size="22px" color="grey-6" />
                            </q-td>
                            """,
                        )
                        table.add_slot(
                            "body-cell-name",
                            """
                            <q-td :props="props">
                              <q-btn dense flat no-caps color="primary" :label="props.value"
                                     @click.stop="$parent.$emit('detail_action', props.row)" />
                            </q-td>
                            """,
                        )
                        table.add_slot(
                            "body-cell-actions",
                            f"""
                            <q-td :props="props" class="text-right">
                              {actions_menu}
                            </q-td>
                            """,
                        )
                        table.on("detail_action", lambda e: open_project_detail(_extract_row_id(e) or -1))
                        table.on("cover_action", lambda e: open_cover_dialog(_extract_row_id(e) or -1))
                        table.on("delete_action", lambda e: delete_project(_extract_row_id(e) or -1))
                        table.on("rowClick", lambda e: open_project_detail(_extract_row_id(e) or -1))

                render_projects()

    @ui.page("/projekte/{project_id}")
    def project_detail_page(project_id: str) -> None:
        try:
            pid = int(project_id)
        except ValueError:
            pid = -1

        mark_dirty, mark_clean, is_dirty, guarded_navigate, clean_and_navigate = create_dirty_guard(
            f"atelierBuddyProjectDirty_{pid}_{uuid.uuid4().hex}"
        )

        with _shell("/projekte", "Projektdetail", navigate_to=guarded_navigate, rerender_path=f"/projekte/{pid}"):
            if pid <= 0:
                with ui.card().classes("bm-card p-4 w-full"):
                    ui.label("Ungültige Projekt-ID")
                return

            with Session(engine) as session:
                project = session.get(Project, pid)

            if not project:
                with ui.card().classes("bm-card p-4 w-full"):
                    ui.label("Projekt nicht gefunden")
                return

            cover_url = to_files_url(project.cover_image_path)

            with ui.card().classes("bm-card p-4 w-full"):
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label(project.name).classes("text-3xl font-semibold")
                    ui.button("Zurück zu Projekten", icon="arrow_back", on_click=lambda: guarded_navigate("/projekte")).props(
                        "flat"
                    )

                with ui.element("div").classes("bm-detail-grid w-full"):
                    with ui.column().classes("bm-detail-preview gap-3"):
                        if cover_url:
                            ui.image(cover_url).classes("w-full max-h-[72vh] object-contain rounded-xl bg-white")
                        ui.label("Cover-Bilder werden als optimiertes WebP gespeichert.").classes("text-xs text-slate-600")

                        async def handle_cover_upload(event: events.MultiUploadEventArguments) -> None:
                            if not event.files:
                                return
                            file_upload = event.files[0]
                            try:
                                new_cover_path = await save_uploaded_work_cover(file_upload, pid)
                                old_cover_path = masterdata.set_project_cover(
                                    project_id=pid,
                                    cover_path=str(new_cover_path),
                                )
                                if old_cover_path and old_cover_path != str(new_cover_path):
                                    safe_delete_file(old_cover_path)
                                ui.notify("Projekt-Cover gespeichert", type="positive")
                                if is_dirty():
                                    ui.notify(
                                        "Ungespeicherte Projektdaten bleiben erhalten; Cover wird nach dem Speichern oder Neuladen sichtbar.",
                                        type="info",
                                    )
                                else:
                                    ui.navigate.to(f"/projekte/{pid}")
                            except Exception as exc:
                                _notify_error("Cover konnte nicht gespeichert werden", exc)

                        upload = ui.upload(
                            multiple=False,
                            auto_upload=True,
                            on_multi_upload=handle_cover_upload,
                            label="Cover ersetzen",
                        ).classes("w-full")
                        upload.props("accept=.jpg,.jpeg,.png,.heic,.heif")

                    with ui.column().classes("bm-detail-form gap-3"):
                        name_input = ui.input("Projektname", value=project.name).classes("w-full")
                        price_input = ui.input(
                            f"Preis ({settings.default_currency}, optional)",
                            value=""
                            if project.price_cents is None
                            else f"{(Decimal(project.price_cents) / Decimal('100')):.2f}".replace(".", ","),
                        ).classes("w-full")
                        created_on_input = ui.input(
                            "Erschaffen am (optional)",
                            value=project.created_on.isoformat() if project.created_on else "",
                        ).props("type=date clearable").classes("w-full")
                        active_input = ui.checkbox("Aktiv", value=project.active)
                        name_input.on("update:model-value", lambda _: mark_dirty())
                        price_input.on("update:model-value", lambda _: mark_dirty())
                        created_on_input.on("update:model-value", lambda _: mark_dirty())
                        active_input.on("update:model-value", lambda _: mark_dirty())
                        mark_clean()

                        with ui.row().classes("w-full justify-between gap-2"):
                            ui.button(
                                "Projekt löschen",
                                icon="delete",
                                on_click=lambda: asyncio.create_task(_delete_and_back()),
                            ).props("flat color=negative")
                            ui.button("Speichern", on_click=lambda: asyncio.create_task(_save_project())).props("color=primary")

                        async def _save_project() -> None:
                            await _flush_active_input(context.client)
                            name = (name_input.value or "").strip()
                            if not name:
                                ui.notify("Projektname fehlt", type="negative")
                                return
                            try:
                                masterdata.update_project(
                                    project_id=pid,
                                    name=name,
                                    active=bool(active_input.value),
                                    price_cents=_parse_money_to_cents(price_input.value),
                                    created_on=_parse_iso_date(created_on_input.value),
                                )
                                ui.notify("Projekt gespeichert", type="positive")
                                await clean_and_navigate(f"/projekte/{pid}")
                            except Exception as exc:
                                _notify_error("Speichern fehlgeschlagen", exc)

                        async def _delete_and_back() -> None:
                            try:
                                old_cover_path = masterdata.delete_project(project_id=pid)
                                safe_delete_file(old_cover_path)
                                ui.notify("Projekt entfernt", type="positive")
                                await clean_and_navigate("/projekte")
                            except Exception as exc:
                                _notify_error("Projekt konnte nicht gelöscht werden", exc)

    def _render_contact_detail(contact_id: int | None) -> None:
        page_key = "new" if contact_id is None else str(contact_id)
        mark_dirty, mark_clean, _, guarded_navigate, clean_and_navigate = create_dirty_guard(
            f"atelierBuddyContactDirty_{page_key}_{uuid.uuid4().hex}"
        )

        with _shell(
            "/kontakte",
            "Kontaktdetail",
            show_page_head=False,
            navigate_to=guarded_navigate,
            rerender_path="/kontakte/neu" if contact_id is None else f"/kontakte/{contact_id}",
        ):
            current_contact: Contact | None = None
            if contact_id is not None:
                with Session(engine) as session:
                    current_contact = session.get(Contact, contact_id)
                if not current_contact:
                    with ui.card().classes("bm-card p-4 w-full"):
                        ui.label("Kontakt nicht gefunden")
                    return

            category_options_map = contact_category_options()
            if not category_options_map:
                with ui.card().classes("bm-card p-4 w-full gap-2"):
                    ui.label("Es gibt noch keine Kontaktkategorien.")
                    ui.button("Zu Kontaktkategorien", on_click=lambda: ui.navigate.to("/kontaktkategorien")).props("flat color=primary")
                return

            default_category_id = next(
                (
                    category_id
                    for category_id, label in category_options_map.items()
                    if label == DEFAULT_CONTACT_CATEGORY_NAME
                ),
                next(iter(category_options_map)),
            )
            selected_category_id = (
                current_contact.contact_category_id if current_contact is not None else default_category_id
            )
            title = _contact_display_name(current_contact) if current_contact is not None else "Neuer Kontakt"

            with ui.card().classes("bm-card bm-detail-card p-4 w-full"):
                with ui.row().classes("bm-detail-toolbar w-full items-center justify-between"):
                    with ui.row().classes("items-center gap-2"):
                        back_btn = ui.button(
                            icon="close",
                            on_click=lambda: guarded_navigate("/kontakte"),
                        ).props("flat round dense").classes("bm-icon-action-btn")
                        back_btn.tooltip("Zurück zu Kontakten")
                    with ui.row().classes("items-center gap-2"):
                        if current_contact is not None:
                            delete_btn = ui.button(
                                icon="delete_outline",
                                on_click=lambda: confirm_delete_contact(),
                            ).props("flat round dense").classes("bm-icon-action-btn bm-icon-action-btn--danger")
                            delete_btn.tooltip("Kontakt löschen")
                        save_btn = ui.button(
                            icon="save",
                            on_click=lambda: save_contact(),
                        ).props("flat round dense").classes("bm-icon-action-btn bm-icon-action-btn--primary")
                        save_btn.tooltip("Speichern")

                ui.label(title).classes("text-xl font-semibold mb-2")

                with ui.column().classes("bm-detail-form gap-3"):
                    contact_fields = build_contact_inputs(
                        current_contact=current_contact,
                        category_options_map=category_options_map,
                        selected_category_id=selected_category_id,
                        include_category=True,
                        include_extended_fields=True,
                        include_notes=True,
                    )

                    for field in contact_fields.values():
                        if field is not None:
                            field.on("update:model-value", lambda _: mark_dirty())

                mark_clean()

                async def save_contact() -> None:
                    await _flush_active_input(context.client)
                    category_id = contact_fields["contact_category_id"].value
                    if not isinstance(category_id, int):
                        ui.notify("Kontaktkategorie fehlt", type="negative")
                        return
                    payload = contact_form_values(contact_fields)
                    try:
                        if current_contact is None:
                            masterdata.create_contact(contact_category_id=category_id, **payload)
                            ui.notify("Kontakt angelegt", type="positive")
                        else:
                            masterdata.update_contact(
                                contact_id=current_contact.id or -1,
                                contact_category_id=category_id,
                                **payload,
                            )
                            ui.notify("Kontakt gespeichert", type="positive")
                        await clean_and_navigate("/kontakte")
                    except Exception as exc:
                        _notify_error("Kontakt konnte nicht gespeichert werden", exc)

                async def delete_contact() -> None:
                    if current_contact is None or current_contact.id is None:
                        return
                    try:
                        masterdata.delete_contact(contact_id=current_contact.id)
                        ui.notify("Kontakt gelöscht", type="positive")
                        await clean_and_navigate("/kontakte")
                    except Exception as exc:
                        _notify_error("Kontakt konnte nicht gelöscht werden", exc)

                def confirm_delete_contact() -> None:
                    with ui.dialog() as dialog, ui.card().classes("p-4 w-[480px] max-w-full"):
                        ui.label("Kontakt endgültig löschen?").classes("text-lg font-semibold")
                        ui.label("Dieser Vorgang kann nicht rückgängig gemacht werden.").classes("text-sm text-slate-600")
                        with ui.row().classes("w-full justify-end gap-2"):
                            ui.button("Abbrechen", on_click=dialog.close).props("flat")

                            async def _confirm() -> None:
                                dialog.close()
                                await delete_contact()

                            ui.button("Endgültig löschen", on_click=_confirm).props("color=negative")
                    dialog.open()

    @ui.page("/kontakte/neu")
    def contact_create_page() -> None:
        _render_contact_detail(None)

    @ui.page("/kontakte/{contact_id}")
    def contact_detail_page(contact_id: str) -> None:
        try:
            cid = int(contact_id)
        except ValueError:
            cid = -1
        if cid <= 0:
            with _shell("/kontakte", "Kontaktdetail"):
                with ui.card().classes("bm-card p-4 w-full"):
                    ui.label("Ungültige Kontakt-ID")
            return
        _render_contact_detail(cid)

    @ui.page("/kontakte")
    def contacts_page() -> None:
        with _shell("/kontakte", "Kontakte"):
            with ui.card().classes("bm-card p-4 w-full"):
                with ui.row().classes("w-full items-center justify-between gap-3 wrap"):
                    ui.label("Kontakte verwalten").classes("text-lg font-semibold")
                    ui.button("Kontakt anlegen", icon="add", on_click=lambda: ui.navigate.to("/kontakte/neu")).props(
                        "color=primary"
                    )

                with ui.row().classes("w-full items-end gap-2 wrap"):
                    search_input = ui.input("Suche").props("clearable").classes("grow bm-filter-field")
                    category_filter = ui.select(
                        {"__all__": "Alle Kategorien"},
                        value="__all__",
                        label="Kategorie",
                    ).classes("w-64 bm-filter-field")

                contacts_column = ui.column().classes("w-full gap-2")

                def refresh_category_filter_options() -> None:
                    current_value = str(category_filter.value or "__all__")
                    options: dict[str, str] = {"__all__": "Alle Kategorien"}
                    for category_id, label in contact_category_options().items():
                        options[str(category_id)] = label
                    if current_value not in options:
                        current_value = "__all__"
                    category_filter.set_options(options, value=current_value)

                def open_contact_detail(contact_id: int) -> None:
                    if contact_id <= 0:
                        return
                    ui.navigate.to(f"/kontakte/{contact_id}")

                def delete_contact(contact_id: int) -> None:
                    with ui.dialog() as dialog, ui.card().classes("p-4 w-[480px] max-w-full"):
                        ui.label("Kontakt endgültig löschen?").classes("text-lg font-semibold")
                        ui.label("Dieser Vorgang kann nicht rückgängig gemacht werden.").classes("text-sm text-slate-600")

                        def confirm_delete() -> None:
                            try:
                                masterdata.delete_contact(contact_id=contact_id)
                                ui.notify("Kontakt gelöscht", type="positive")
                                dialog.close()
                                render_contacts()
                            except Exception as exc:
                                _notify_error("Kontakt konnte nicht gelöscht werden", exc)

                        with ui.row().classes("w-full justify-end gap-2"):
                            ui.button("Abbrechen", on_click=dialog.close).props("flat")
                            ui.button("Endgültig löschen", on_click=confirm_delete).props("color=negative")
                    dialog.open()

                def matches_search(contact: Contact, search_value: str) -> bool:
                    if not search_value:
                        return True
                    haystack = " ".join(
                        [
                            contact.given_name or "",
                            contact.family_name or "",
                            contact.organisation or "",
                            contact.email or "",
                            contact.phone or "",
                            contact.mobile or "",
                            contact.street or "",
                            contact.house_number or "",
                            contact.address_extra or "",
                            contact.postal_code or "",
                            contact.city or "",
                            _contact_country_label(contact.country),
                            contact.notes or "",
                        ]
                    ).casefold()
                    return search_value in haystack

                def render_contacts() -> None:
                    selected_category = str(category_filter.value or "__all__")
                    search_value = (search_input.value or "").strip().casefold()
                    with Session(engine) as session:
                        stmt = select(Contact).options(selectinload(Contact.contact_category))
                        if selected_category != "__all__":
                            try:
                                stmt = stmt.where(Contact.contact_category_id == int(selected_category))
                            except ValueError:
                                pass
                        contacts = [contact for contact in session.exec(stmt).all() if matches_search(contact, search_value)]

                    contacts.sort(key=_contact_sort_key)
                    contacts_column.clear()
                    with contacts_column:
                        if not contacts:
                            ui.label("Noch keine Kontakte vorhanden.")
                            return

                        rows = []
                        for contact in contacts:
                            if contact.id is None:
                                continue
                            category_name = contact.contact_category.name if contact.contact_category else "-"
                            phone_parts = [part for part in (contact.phone, contact.mobile) if part]
                            rows.append(
                                {
                                    "id": contact.id,
                                    "name": _contact_display_name(contact),
                                    "category": category_name,
                                    "organisation": contact.organisation or "-",
                                    "location": _contact_location_label(contact),
                                    "reachability": contact.email or (" / ".join(phone_parts) if phone_parts else "-"),
                                }
                            )

                        columns = [
                            {"name": "name", "label": "Kontakt", "field": "name", "align": "left", "sortable": True},
                            {"name": "category", "label": "Kategorie", "field": "category", "align": "left"},
                            {"name": "organisation", "label": "Organisation", "field": "organisation", "align": "left"},
                            {"name": "location", "label": "Ort", "field": "location", "align": "left"},
                            {"name": "reachability", "label": "Kontaktweg", "field": "reachability", "align": "left"},
                            {"name": "actions", "label": "Aktionen", "field": "actions", "align": "right"},
                        ]
                        actions_menu = """
                        <q-btn flat round dense icon="more_vert" @click.stop>
                          <q-menu auto-close>
                            <q-list dense style="min-width: 220px">
                              <q-item clickable @click="$parent.$emit('edit_action', props.row)">
                                <q-item-section avatar><q-icon name="edit" /></q-item-section>
                                <q-item-section><q-item-label>Bearbeiten</q-item-label></q-item-section>
                              </q-item>
                              <q-item clickable @click="$parent.$emit('delete_action', props.row)">
                                <q-item-section avatar><q-icon name="delete" color="negative" /></q-item-section>
                                <q-item-section><q-item-label>Kontakt löschen</q-item-label></q-item-section>
                              </q-item>
                            </q-list>
                          </q-menu>
                        </q-btn>
                        """
                        for row in rows:
                            organisation = row.get("organisation") or "-"
                            reachability = row.get("reachability") or "-"
                            row["mobile_title"] = row.get("name") or "-"
                            row["mobile_title_note"] = organisation if organisation != "-" else ""
                            row["mobile_primary_left"] = row.get("category") or "-"
                            row["mobile_primary_right"] = row.get("location") or "-"
                            row["mobile_secondary"] = reachability if reachability != "-" else organisation

                        table = _responsive_erp_table(
                            columns=columns,
                            rows=rows,
                            row_key="id",
                            rows_per_page=20,
                            mobile_actions_slot=actions_menu,
                        )
                        table.add_slot(
                            "body-cell-actions",
                            f"""
                            <q-td :props="props" class="text-right">
                              {actions_menu}
                            </q-td>
                            """,
                        )
                        table.on("rowClick", lambda e: open_contact_detail(_extract_row_id(e) or -1))
                        table.on("edit_action", lambda e: open_contact_detail(_extract_row_id(e) or -1))
                        table.on("delete_action", lambda e: delete_contact(_extract_row_id(e) or -1))

                search_input.on_value_change(lambda _: render_contacts())
                category_filter.on_value_change(lambda _: render_contacts())
                refresh_category_filter_options()
                render_contacts()

    @ui.page("/kontaktkategorien")
    def contact_categories_page() -> None:
        icon_option_map = {icon: _icon_option_html(label, icon) for label, icon in CONTACT_CATEGORY_ICON_OPTIONS}

        with _shell("/kontaktkategorien", "Kontaktkategorien"):
            with ui.card().classes("bm-card p-4 w-full"):
                def open_create_contact_category_dialog() -> None:
                    with ui.dialog() as dialog, ui.card().classes("p-4 w-[560px] max-w-full"):
                        ui.label("Neue Kontaktkategorie").classes("text-lg font-semibold")
                        name_input = ui.input("Name").classes("w-full")
                        icon_select = ui.select(
                            icon_option_map,
                            value=DEFAULT_CONTACT_CATEGORY_ICON,
                            label="Symbol",
                        ).classes("w-full")
                        icon_select.props("options-html display-value-html")

                        def add_category() -> None:
                            name = (name_input.value or "").strip()
                            if not name:
                                ui.notify("Name fehlt", type="negative")
                                return
                            try:
                                _, created = masterdata.create_or_update_contact_category(
                                    name=name,
                                    icon=str(icon_select.value or DEFAULT_CONTACT_CATEGORY_ICON),
                                )
                                ui.notify(
                                    "Kontaktkategorie angelegt"
                                    if created
                                    else "Kontaktkategorie existierte bereits und wurde aktualisiert",
                                    type="positive",
                                )
                                dialog.close()
                                render_contact_categories()
                            except Exception as exc:
                                _notify_error("Kontaktkategorie konnte nicht angelegt werden", exc)

                        with ui.row().classes("w-full justify-end gap-2"):
                            ui.button("Abbrechen", on_click=dialog.close).props("flat")
                            ui.button("Anlegen", on_click=add_category).props("color=primary")
                    dialog.open()

                with ui.row().classes("w-full items-center justify-between gap-3 wrap"):
                    ui.label("Kontaktkategorien verwalten").classes("text-lg font-semibold")
                    ui.button(
                        "Kontaktkategorie anlegen",
                        icon="add",
                        on_click=open_create_contact_category_dialog,
                    ).props("color=primary")

                category_column = ui.column().classes("w-full gap-2")

                def open_edit_contact_category_dialog(category_id: int) -> None:
                    with Session(engine) as session:
                        category = session.get(ContactCategory, category_id)
                        if not category:
                            ui.notify("Kontaktkategorie nicht gefunden", type="negative")
                            return
                        current_name = category.name
                        current_icon = category.icon or DEFAULT_CONTACT_CATEGORY_ICON

                    with ui.dialog() as dialog, ui.card().classes("p-4 w-[560px] max-w-full"):
                        ui.label("Kontaktkategorie bearbeiten").classes("text-lg font-semibold")
                        name_edit = ui.input("Name", value=current_name).classes("w-full")
                        icon_edit = ui.select(
                            icon_option_map,
                            value=current_icon,
                            label="Symbol",
                        ).classes("w-full")
                        icon_edit.props("options-html display-value-html")

                        def confirm_delete() -> None:
                            with ui.dialog() as confirm_dialog, ui.card().classes("p-4 w-[500px] max-w-full"):
                                ui.label("Kontaktkategorie endgültig löschen?").classes("text-lg font-semibold")
                                ui.label(
                                    "Dieser Vorgang kann nicht rückgängig gemacht werden. "
                                    "Wenn die Kategorie noch verwendet wird, wird das Löschen blockiert."
                                ).classes("text-sm text-slate-600")

                                def run_delete() -> None:
                                    try:
                                        masterdata.delete_contact_category(category_id=category_id)
                                        ui.notify("Kontaktkategorie gelöscht", type="positive")
                                        confirm_dialog.close()
                                        dialog.close()
                                        render_contact_categories()
                                    except Exception as exc:
                                        _notify_error("Kontaktkategorie konnte nicht gelöscht werden", exc)

                                with ui.row().classes("w-full justify-end gap-2"):
                                    ui.button("Abbrechen", on_click=confirm_dialog.close).props("flat")
                                    ui.button("Endgültig löschen", on_click=run_delete).props("color=negative")
                            confirm_dialog.open()

                        def save_category() -> None:
                            name = (name_edit.value or "").strip()
                            if not name:
                                ui.notify("Name fehlt", type="negative")
                                return
                            try:
                                masterdata.update_contact_category(
                                    category_id=category_id,
                                    name=name,
                                    icon=str(icon_edit.value or DEFAULT_CONTACT_CATEGORY_ICON),
                                )
                                ui.notify("Kontaktkategorie gespeichert", type="positive")
                                dialog.close()
                                render_contact_categories()
                            except Exception as exc:
                                _notify_error("Speichern fehlgeschlagen", exc)

                        with ui.row().classes("w-full justify-between gap-2"):
                            ui.button("Löschen", on_click=confirm_delete).props("flat color=negative")
                            ui.button("Abbrechen", on_click=dialog.close).props("flat")
                            ui.button("Speichern", on_click=save_category).props("color=primary")
                    dialog.open()

                def render_contact_categories() -> None:
                    with Session(engine) as session:
                        categories = list(session.exec(select(ContactCategory).order_by(ContactCategory.name)).all())
                        category_ids = [item.id for item in categories if item.id is not None]
                        used_category_ids = {
                            item
                            for item in session.exec(
                                select(Contact.contact_category_id).where(Contact.contact_category_id.in_(category_ids or [-1]))
                            ).all()
                            if isinstance(item, int)
                        }

                    category_column.clear()
                    with category_column:
                        if not categories:
                            ui.label("Noch keine Kontaktkategorien vorhanden.")
                            return

                        rows = [
                            {
                                "id": category.id,
                                "name": category.name,
                                "icon": category.icon or DEFAULT_CONTACT_CATEGORY_ICON,
                                "used": (category.id in used_category_ids) if category.id is not None else False,
                                "mobile_title": category.name,
                                "mobile_primary_left": "Kontaktkategorie",
                                "mobile_primary_right": "verwendet" if (category.id in used_category_ids) else "frei",
                                "mobile_secondary": f"Symbol: {category.icon or DEFAULT_CONTACT_CATEGORY_ICON}",
                                "mobile_badge": "verwendet" if (category.id in used_category_ids) else "frei",
                                "mobile_badge_color": "warning" if (category.id in used_category_ids) else "positive",
                            }
                            for category in categories
                            if category.id is not None
                        ]
                        columns = [
                            {"name": "icon", "label": "Symbol", "field": "icon", "align": "left"},
                            {"name": "name", "label": "Kontaktkategorie", "field": "name", "align": "left", "sortable": True},
                            {"name": "used", "label": "Verwendet", "field": "used", "align": "left"},
                            {"name": "actions", "label": "Aktionen", "field": "actions", "align": "right"},
                        ]
                        actions_menu = """
                        <q-btn flat round dense icon="more_vert" @click.stop>
                          <q-menu auto-close>
                            <q-list dense style="min-width: 240px">
                              <q-item clickable @click="$parent.$emit('edit_action', props.row)">
                                <q-item-section avatar><q-icon name="edit" /></q-item-section>
                                <q-item-section><q-item-label>Bearbeiten</q-item-label></q-item-section>
                              </q-item>
                              <q-item clickable @click="$parent.$emit('delete_action', props.row)">
                                <q-item-section avatar><q-icon name="delete" color="negative" /></q-item-section>
                                <q-item-section><q-item-label>Löschen</q-item-label></q-item-section>
                              </q-item>
                            </q-list>
                          </q-menu>
                        </q-btn>
                        """
                        table = _responsive_erp_table(
                            columns=columns,
                            rows=rows,
                            row_key="id",
                            rows_per_page=20,
                            mobile_actions_slot=actions_menu,
                        )
                        table.add_slot(
                            "body-cell-icon",
                            """
                            <q-td :props="props">
                              <q-icon :name="props.row.icon" size="20px" />
                            </q-td>
                            """,
                        )
                        table.add_slot(
                            "body-cell-used",
                            """
                            <q-td :props="props">
                              <q-badge :color="props.row.used ? 'warning' : 'positive'" outline>
                                {{ props.row.used ? 'ja' : 'nein' }}
                              </q-badge>
                            </q-td>
                            """,
                        )
                        table.add_slot(
                            "body-cell-actions",
                            f"""
                            <q-td :props="props" class="text-right">
                              {actions_menu}
                            </q-td>
                            """,
                        )
                        table.on("rowClick", lambda e: open_edit_contact_category_dialog(_extract_row_id(e) or -1))
                        table.on("edit_action", lambda e: open_edit_contact_category_dialog(_extract_row_id(e) or -1))
                        table.on("delete_action", lambda e: open_edit_contact_category_dialog(_extract_row_id(e) or -1))
                render_contact_categories()

    @ui.page("/lieferanten")
    def suppliers_page() -> None:
        with _shell("/lieferanten", "Anbieter"):
            with ui.card().classes("bm-card p-4 w-full"):
                def open_create_supplier_dialog() -> None:
                    with ui.dialog() as dialog, ui.card().classes("p-4 w-[520px] max-w-full"):
                        ui.label("Neuer Anbieter").classes("text-lg font-semibold")
                        name_input = ui.input("Anbietername").classes("w-full")
                        active_input = ui.checkbox("Aktiv", value=True)

                        def add_supplier() -> None:
                            name = (name_input.value or "").strip()
                            if not name:
                                ui.notify("Anbietername fehlt", type="negative")
                                return
                            try:
                                _, created = masterdata.create_or_update_supplier(
                                    name=name,
                                    active=bool(active_input.value),
                                )
                                ui.notify(
                                    "Anbieter angelegt" if created else "Anbieter existierte bereits und wurde aktualisiert",
                                    type="positive",
                                )
                                dialog.close()
                                render_suppliers()
                            except Exception as exc:
                                _notify_error("Anbieter konnte nicht angelegt werden", exc)

                        with ui.row().classes("w-full justify-end gap-2"):
                            ui.button("Abbrechen", on_click=dialog.close).props("flat")
                            ui.button("Anlegen", on_click=add_supplier).props("color=primary")
                    dialog.open()

                with ui.row().classes("w-full justify-end"):
                    ui.button("Anbieter anlegen", icon="add", on_click=open_create_supplier_dialog).props("color=primary")

                suppliers_column = ui.column().classes("w-full gap-2")

                def delete_supplier(supplier_id: int) -> None:
                    try:
                        masterdata.delete_supplier(supplier_id=supplier_id)
                        ui.notify("Anbieter entfernt", type="positive")
                        render_suppliers()
                    except Exception as exc:
                        _notify_error("Anbieter konnte nicht gelöscht werden", exc)

                def open_edit_supplier_dialog(supplier_id: int) -> None:
                    with Session(engine) as session:
                        supplier = session.get(Supplier, supplier_id)
                        if not supplier:
                            ui.notify("Anbieter nicht gefunden", type="negative")
                            return
                        current_name = supplier.name
                        current_active = supplier.active

                    with ui.dialog() as dialog, ui.card().classes("p-4 w-[520px] max-w-full"):
                        ui.label("Anbieter bearbeiten").classes("text-lg font-semibold")
                        name_edit = ui.input("Name", value=current_name).classes("w-full")
                        active_edit = ui.checkbox("Aktiv", value=current_active)

                        def save_supplier() -> None:
                            name = (name_edit.value or "").strip()
                            if not name:
                                ui.notify("Anbietername fehlt", type="negative")
                                return

                            try:
                                masterdata.update_supplier(
                                    supplier_id=supplier_id,
                                    name=name,
                                    active=bool(active_edit.value),
                                )
                                ui.notify("Anbieter gespeichert", type="positive")
                                dialog.close()
                                render_suppliers()
                            except Exception as exc:
                                _notify_error("Speichern fehlgeschlagen", exc)

                        with ui.row().classes("w-full justify-end gap-2"):
                            ui.button("Abbrechen", on_click=dialog.close).props("flat")
                            ui.button("Speichern", on_click=save_supplier).props("color=primary")
                    dialog.open()

                def render_suppliers() -> None:
                    with Session(engine) as session:
                        suppliers = list(session.exec(select(Supplier).order_by(Supplier.name)).all())

                    suppliers_column.clear()
                    with suppliers_column:
                        if not suppliers:
                            ui.label("Noch keine Anbieter vorhanden.")
                            return

                        rows = [
                            {
                                "id": supplier.id,
                                "name": supplier.name,
                                "status": "aktiv" if supplier.active else "inaktiv",
                                "mobile_title": supplier.name,
                                "mobile_primary_left": "Anbieter",
                                "mobile_primary_right": "aktiv" if supplier.active else "inaktiv",
                                "mobile_secondary": "Stammdatensatz",
                                "mobile_badge": "aktiv" if supplier.active else "inaktiv",
                                "mobile_badge_color": "positive" if supplier.active else "grey-7",
                            }
                            for supplier in suppliers
                            if supplier.id is not None
                        ]
                        columns = [
                            {"name": "name", "label": "Anbieter", "field": "name", "align": "left", "sortable": True},
                            {"name": "status", "label": "Status", "field": "status", "align": "left"},
                            {"name": "actions", "label": "Aktionen", "field": "actions", "align": "right"},
                        ]
                        actions_menu = """
                        <q-btn flat round dense icon="more_vert" @click.stop>
                          <q-menu auto-close>
                            <q-list dense style="min-width: 220px">
                              <q-item clickable @click="$parent.$emit('edit_action', props.row)">
                                <q-item-section avatar><q-icon name="edit" /></q-item-section>
                                <q-item-section><q-item-label>Bearbeiten</q-item-label></q-item-section>
                              </q-item>
                              <q-item clickable @click="$parent.$emit('delete_action', props.row)">
                                <q-item-section avatar><q-icon name="delete" color="negative" /></q-item-section>
                                <q-item-section><q-item-label>Anbieter löschen</q-item-label></q-item-section>
                              </q-item>
                            </q-list>
                          </q-menu>
                        </q-btn>
                        """
                        table = _responsive_erp_table(
                            columns=columns,
                            rows=rows,
                            row_key="id",
                            rows_per_page=20,
                            mobile_actions_slot=actions_menu,
                        )
                        table.add_slot(
                            "body-cell-actions",
                            f"""
                            <q-td :props="props" class="text-right">
                              {actions_menu}
                            </q-td>
                            """,
                        )
                        table.on("edit_action", lambda e: open_edit_supplier_dialog(_extract_row_id(e) or -1))
                        table.on("delete_action", lambda e: delete_supplier(_extract_row_id(e) or -1))
                        table.on("rowClick", lambda e: open_edit_supplier_dialog(_extract_row_id(e) or -1))

                render_suppliers()

    @ui.page("/kategorien")
    def categories_page() -> None:
        icon_option_map = {icon: _icon_option_html(label, icon) for label, icon in COST_TYPE_ICON_OPTIONS}
        category_view_mode = "active"

        with _shell("/kategorien", "Kostenkategorien"):
            with ui.card().classes("bm-card p-4 w-full"):
                def open_create_cost_type_dialog() -> None:
                    with ui.dialog() as dialog, ui.card().classes("p-4 w-[560px] max-w-full"):
                        ui.label("Neue Kostenkategorie").classes("text-lg font-semibold")
                        name_input = ui.input("Name").classes("w-full")
                        icon_select = ui.select(
                            icon_option_map,
                            value=DEFAULT_COST_TYPE_ICON,
                            label="Symbol",
                        ).classes("w-full")
                        icon_select.props("options-html display-value-html")

                        def add_category() -> None:
                            name = (name_input.value or "").strip()
                            if not name:
                                ui.notify("Name fehlt", type="negative")
                                return
                            icon = str(icon_select.value or DEFAULT_COST_TYPE_ICON)
                            try:
                                _, created = masterdata.create_or_update_cost_type(name=name, icon=icon)
                                ui.notify(
                                    "Kostenkategorie angelegt"
                                    if created
                                    else "Kostenkategorie existierte bereits und wurde aktualisiert",
                                    type="positive",
                                )
                                dialog.close()
                                set_category_view("active")
                            except Exception as exc:
                                _notify_error("Kostenkategorie konnte nicht angelegt werden", exc)

                        with ui.row().classes("w-full justify-end gap-2"):
                            ui.button("Abbrechen", on_click=dialog.close).props("flat")
                            ui.button("Anlegen", on_click=add_category).props("color=primary")
                    dialog.open()

                with ui.row().classes("w-full items-center justify-between"):
                    with ui.row().classes("gap-2"):
                        active_category_button = ui.button("Kostenkategorien").props("unelevated no-caps").classes(
                            "bm-view-mode-btn bm-segment-btn"
                        )
                        archived_category_button = ui.button("Archivierte Kostenkategorien").props(
                            "unelevated no-caps"
                        ).classes("bm-view-mode-btn bm-segment-btn")
                    ui.button("Kostenkategorie anlegen", icon="add", on_click=open_create_cost_type_dialog).props("color=primary")

                category_column = ui.column().classes("w-full gap-2")

                def apply_category_view_styles() -> None:
                    active_category_button.classes(remove="bm-segment-btn--active bm-segment-btn--inactive")
                    archived_category_button.classes(remove="bm-segment-btn--active bm-segment-btn--inactive")
                    if category_view_mode == "active":
                        active_category_button.classes(add="bm-segment-btn--active")
                        archived_category_button.classes(add="bm-segment-btn--inactive")
                    else:
                        active_category_button.classes(add="bm-segment-btn--inactive")
                        archived_category_button.classes(add="bm-segment-btn--active")

                def run_category_primary_action(category_id: int) -> None:
                    if category_id <= 0:
                        return
                    try:
                        if category_view_mode == "archived":
                            masterdata.restore_cost_type(category_id=category_id)
                            ui.notify("Kostenkategorie wiederhergestellt", type="positive")
                            render_categories()
                            return
                        action = masterdata.archive_or_delete_cost_type(category_id=category_id)
                        if action == "archived":
                            ui.notify("Kostenkategorie archiviert", type="positive")
                        else:
                            ui.notify("Unbenutzte Kostenkategorie gelöscht", type="positive")
                    except Exception as exc:
                        _notify_error("Aktion auf Kostenkategorie fehlgeschlagen", exc)
                    render_categories()

                def open_edit_category_dialog(category_id: int) -> None:
                    with Session(engine) as session:
                        category = session.get(CostType, category_id)
                        if not category:
                            ui.notify("Kostenkategorie nicht gefunden", type="negative")
                            return
                        current_name = category.name
                        current_icon = category.icon or DEFAULT_COST_TYPE_ICON
                        category_is_active = bool(category.active)

                    with ui.dialog() as dialog, ui.card().classes("p-4 w-[560px] max-w-full"):
                        ui.label("Kostenkategorie bearbeiten").classes("text-lg font-semibold")
                        name_edit = ui.input("Name", value=current_name).classes("w-full")
                        icon_edit = ui.select(icon_option_map, value=current_icon, label="Symbol").classes("w-full")
                        icon_edit.props("options-html display-value-html")
                        show_archived_subcategories = False
                        active_subcategory_column = ui.column().classes("w-full gap-2")
                        new_subcategory_input = ui.input("Neue Unterkategorie").classes("w-full")
                        with ui.row().classes("w-full gap-2 items-end wrap"):
                            ui.button("Unterkategorie hinzufügen", icon="add", on_click=lambda: add_subcategory()).props("flat")
                        archived_toggle_row = ui.row().classes("w-full")
                        archived_subcategory_column = ui.column().classes("w-full gap-2")
                        archived_toggle_button: ui.button | None = None

                        def render_subcategories() -> None:
                            with Session(engine) as session:
                                items = list(
                                    session.exec(
                                        select(CostSubcategory)
                                        .where(CostSubcategory.cost_type_id == category_id)
                                        .order_by(CostSubcategory.is_system_default.desc(), CostSubcategory.name)
                                    ).all()
                                )
                                used_subcategory_ids = {
                                    item
                                    for item in session.exec(
                                        select(CostAllocation.cost_subcategory_id).where(
                                            CostAllocation.cost_subcategory_id.in_([row.id for row in items if row.id is not None] or [-1])
                                        )
                                    ).all()
                                    if isinstance(item, int)
                                }

                            active_items = [item for item in items if item.active]
                            archived_items = [item for item in items if not item.active]

                            active_subcategory_column.clear()
                            with active_subcategory_column:
                                ui.label("Aktive Unterkategorien").classes("text-sm font-semibold")
                                if not active_items:
                                    ui.label("Keine aktiven Unterkategorien.").classes("text-sm text-slate-600")

                                for item in active_items:
                                    with ui.row().classes("w-full items-center justify-between bm-card p-2"):
                                        with ui.row().classes("items-center gap-2"):
                                            ui.label(item.name).classes("font-medium")
                                            if item.is_system_default:
                                                ui.badge("Standard").props("color=primary outline")

                                        if item.is_system_default:
                                            ui.icon("lock", size="18px")
                                        else:
                                            is_used = item.id in used_subcategory_ids
                                            ui.button(
                                                icon="archive" if is_used else "delete_outline",
                                                on_click=lambda sid=item.id: run_subcategory_primary_action(sid),
                                            ).props(f"flat round dense color={'warning' if is_used else 'negative'}")

                                    if (item.id in used_subcategory_ids) and not item.is_system_default:
                                        ui.label("Wird verwendet und wird beim Entfernen archiviert.").classes(
                                            "text-xs text-slate-600"
                                        )

                            archived_subcategory_column.clear()
                            if show_archived_subcategories:
                                with archived_subcategory_column:
                                    ui.separator().classes("w-full my-1")
                                    ui.label("Archivierte Unterkategorien").classes("text-sm font-semibold")
                                    if not archived_items:
                                        ui.label("Keine archivierten Unterkategorien.").classes("text-sm text-slate-600")
                                    for item in archived_items:
                                        with ui.row().classes("w-full items-center justify-between bm-card p-2"):
                                            with ui.row().classes("items-center gap-2"):
                                                ui.label(item.name).classes("font-medium")
                                                if item.is_system_default:
                                                    ui.badge("Standard").props("color=primary outline")
                                            restore_button = ui.button(
                                                icon="restore",
                                                on_click=lambda sid=item.id: restore_subcategory(sid),
                                            ).props("flat round dense color=positive")
                                            if not category_is_active:
                                                restore_button.disable()
                                    if not category_is_active and archived_items:
                                        ui.label("Wiederherstellen erst möglich, wenn die Kostenkategorie aktiv ist.").classes(
                                            "text-xs text-amber-700"
                                        )

                        def render_archived_toggle() -> None:
                            nonlocal archived_toggle_button
                            archived_toggle_row.clear()
                            with archived_toggle_row:
                                archived_toggle_button = ui.button(
                                    "Archivierte ausblenden" if show_archived_subcategories else "Zeige archivierte",
                                    icon="expand_less" if show_archived_subcategories else "expand_more",
                                    on_click=lambda: toggle_archived_view(),
                                ).props("flat no-caps")

                        def toggle_archived_view() -> None:
                            nonlocal show_archived_subcategories
                            show_archived_subcategories = not show_archived_subcategories
                            render_archived_toggle()
                            render_subcategories()

                        def add_subcategory() -> None:
                            nonlocal show_archived_subcategories
                            name = (new_subcategory_input.value or "").strip()
                            if not name:
                                ui.notify("Unterkategorie-Name fehlt", type="negative")
                                return
                            try:
                                _, created = masterdata.add_subcategory(category_id=category_id, name=name)
                                new_subcategory_input.value = ""
                                ui.notify("Unterkategorie angelegt" if created else "Unterkategorie wiederhergestellt", type="positive")
                                show_archived_subcategories = False
                                render_archived_toggle()
                                render_subcategories()
                            except Exception as exc:
                                _notify_error("Unterkategorie konnte nicht angelegt werden", exc)

                        def run_subcategory_primary_action(subcategory_id: int | None) -> None:
                            if not subcategory_id:
                                return
                            try:
                                action = masterdata.subcategory_primary_action(subcategory_id=subcategory_id)
                                if action == "archived":
                                    ui.notify("Unterkategorie archiviert", type="positive")
                                else:
                                    ui.notify("Unterkategorie gelöscht", type="positive")
                                render_subcategories()
                            except Exception as exc:
                                _notify_error("Aktion auf Unterkategorie fehlgeschlagen", exc)

                        def restore_subcategory(subcategory_id: int | None) -> None:
                            if not subcategory_id:
                                return
                            try:
                                masterdata.restore_subcategory(subcategory_id=subcategory_id)
                                ui.notify("Unterkategorie wiederhergestellt", type="positive")
                                render_subcategories()
                            except Exception as exc:
                                _notify_error("Unterkategorie konnte nicht wiederhergestellt werden", exc)

                        def save_category() -> None:
                            name = (name_edit.value or "").strip()
                            if not name:
                                ui.notify("Name fehlt", type="negative")
                                return
                            try:
                                masterdata.update_cost_type(
                                    category_id=category_id,
                                    name=name,
                                    icon=str(icon_edit.value or DEFAULT_COST_TYPE_ICON),
                                )
                                ui.notify("Kostenkategorie gespeichert", type="positive")
                                dialog.close()
                                render_categories()
                            except Exception as exc:
                                _notify_error("Speichern fehlgeschlagen", exc)

                        render_archived_toggle()
                        render_subcategories()

                        with ui.row().classes("w-full justify-end gap-2"):
                            ui.button("Abbrechen", on_click=dialog.close).props("flat")
                            ui.button("Speichern", on_click=save_category).props("color=primary")
                    dialog.open()

                def render_categories() -> None:
                    with Session(engine) as session:
                        stmt = select(CostType).order_by(CostType.name)
                        if category_view_mode == "active":
                            stmt = stmt.where(CostType.active.is_(True))
                        else:
                            stmt = stmt.where(CostType.active.is_(False))
                        categories = list(session.exec(stmt).all())
                        category_ids = [item.id for item in categories if item.id is not None]
                        subcategories = list(
                            session.exec(
                                select(CostSubcategory).where(CostSubcategory.cost_type_id.in_(category_ids or [-1]))
                            ).all()
                        )
                        used_category_ids = {
                            item
                            for item in session.exec(
                                select(CostAllocation.cost_type_id).where(CostAllocation.cost_type_id.in_(category_ids or [-1]))
                            ).all()
                            if isinstance(item, int)
                        }

                    subcategory_count: dict[int, int] = {}
                    for item in subcategories:
                        subcategory_count[item.cost_type_id] = subcategory_count.get(item.cost_type_id, 0) + 1

                    category_column.clear()
                    with category_column:
                        if not categories:
                            empty_label = (
                                "Noch keine aktiven Kostenkategorien vorhanden."
                                if category_view_mode == "active"
                                else "Keine archivierten Kostenkategorien."
                            )
                            ui.label(empty_label)
                            return

                        rows = [
                            {
                                "id": category.id,
                                "name": category.name,
                                "icon": category.icon or DEFAULT_COST_TYPE_ICON,
                                "subcategory_count": subcategory_count.get(category.id or -1, 0),
                                "status": "aktiv" if category.active else "archiviert",
                                "used": (category.id in used_category_ids) if category.id is not None else False,
                                "primary_label": (
                                    "Wiederherstellen"
                                    if category_view_mode == "archived"
                                    else ("Archivieren" if category.id in used_category_ids else "Löschen")
                                ),
                                "primary_icon": (
                                    "restore"
                                    if category_view_mode == "archived"
                                    else ("archive" if category.id in used_category_ids else "delete")
                                ),
                                "primary_color": (
                                    "positive"
                                    if category_view_mode == "archived"
                                    else ("warning" if category.id in used_category_ids else "negative")
                                ),
                                "mobile_title": category.name,
                                "mobile_title_note": f"{subcategory_count.get(category.id or -1, 0)} Unterkategorien",
                                "mobile_primary_left": "aktiv" if category.active else "archiviert",
                                "mobile_primary_right": f"{subcategory_count.get(category.id or -1, 0)} Unterkategorien",
                                "mobile_secondary": (
                                    "Wird verwendet und wird archiviert."
                                    if (category.id in used_category_ids and category.active)
                                    else category.name
                                ),
                                "mobile_badge": "aktiv" if category.active else "archiviert",
                                "mobile_badge_color": "positive" if category.active else "grey-7",
                            }
                            for category in categories
                            if category.id is not None
                        ]
                        columns = [
                            {"name": "icon", "label": "Symbol", "field": "icon", "align": "left"},
                            {
                                "name": "name",
                                "label": "Kostenkategorie",
                                "field": "name",
                                "align": "left",
                                "sortable": True,
                            },
                            {
                                "name": "subcategory_count",
                                "label": "Unterkategorien",
                                "field": "subcategory_count",
                                "align": "right",
                            },
                            {"name": "status", "label": "Status", "field": "status", "align": "left"},
                            {"name": "actions", "label": "Aktionen", "field": "actions", "align": "right"},
                        ]
                        actions_menu = """
                        <q-btn flat round dense icon="more_vert" @click.stop>
                          <q-menu auto-close>
                            <q-list dense style="min-width: 240px">
                              <q-item clickable @click="$parent.$emit('edit_action', props.row)">
                                <q-item-section avatar><q-icon name="edit" /></q-item-section>
                                <q-item-section><q-item-label>Bearbeiten</q-item-label></q-item-section>
                              </q-item>
                              <q-item clickable @click="$parent.$emit('primary_action', props.row)">
                                <q-item-section avatar><q-icon :name="props.row.primary_icon" :color="props.row.primary_color" /></q-item-section>
                                <q-item-section>
                                  <q-item-label>{{ props.row.primary_label }}</q-item-label>
                                  <q-item-label v-if="props.row.used && props.row.status === 'aktiv'" caption>Wird verwendet und wird archiviert.</q-item-label>
                                </q-item-section>
                              </q-item>
                            </q-list>
                          </q-menu>
                        </q-btn>
                        """
                        table = _responsive_erp_table(
                            columns=columns,
                            rows=rows,
                            row_key="id",
                            rows_per_page=20,
                            mobile_actions_slot=actions_menu,
                        )
                        table.add_slot(
                            "body-cell-icon",
                            """
                            <q-td :props="props">
                              <q-icon :name="props.row.icon" size="20px" />
                            </q-td>
                            """,
                        )
                        table.add_slot(
                            "body-cell-status",
                            """
                            <q-td :props="props">
                              <q-badge :color="props.row.status === 'aktiv' ? 'positive' : 'grey-7'" outline>
                                {{ props.row.status }}
                              </q-badge>
                            </q-td>
                            """,
                        )
                        table.add_slot(
                            "body-cell-actions",
                            f"""
                            <q-td :props="props" class="text-right">
                              {actions_menu}
                            </q-td>
                            """,
                        )
                        table.on("rowClick", lambda e: open_edit_category_dialog(_extract_row_id(e) or -1))
                        table.on("edit_action", lambda e: open_edit_category_dialog(_extract_row_id(e) or -1))
                        table.on("primary_action", lambda e: run_category_primary_action(_extract_row_id(e) or -1))

                def set_category_view(next_mode: str) -> None:
                    nonlocal category_view_mode
                    if next_mode == category_view_mode:
                        return
                    category_view_mode = next_mode
                    apply_category_view_styles()
                    render_categories()

                active_category_button.on("click", lambda _: set_category_view("active"))
                archived_category_button.on("click", lambda _: set_category_view("archived"))
                apply_category_view_styles()
                render_categories()

    @ui.page("/auswertung")
    def report_page() -> None:
        with _shell("/auswertung", "Auswertung"):
            with ui.card().classes("bm-card p-4 w-full gap-3"):
                today = date.today()
                month_start = today.replace(day=1)
                selected_cost_type_id: int | None = None
                selected_cost_type_name: str | None = None
                selected_income_project_id: int | None = None
                selected_income_project_name: str | None = None
                with ui.row().classes("w-full items-end gap-2"):
                    date_from = ui.input("Startdatum", value=month_start.isoformat()).props("type=date clearable").classes(
                        "w-44 bm-filter-field"
                    )
                    date_to = ui.input("Enddatum", value=today.isoformat()).props("type=date clearable").classes(
                        "w-44 bm-filter-field"
                    )
                    ui.button("Auswertung laden", icon="insights", on_click=lambda: render_report()).props(
                        "color=primary"
                    ).classes("bm-filter-btn bm-toolbar-btn")
                income_summary_container = ui.column().classes("w-full gap-2")
                income_projects_container = ui.column().classes("w-full gap-2")
                income_orders_container = ui.column().classes("w-full gap-2")
                summary_container = ui.column().classes("w-full gap-2")
                categories_container = ui.column().classes("w-full gap-2")
                subcategories_container = ui.column().classes("w-full gap-2")

                def amount_state(total_cents: int) -> tuple[str, str]:
                    if total_cents > 0:
                        return "Ausgabe", "bm-amount-expense"
                    if total_cents < 0:
                        return "Einnahme", "bm-amount-income"
                    return "Neutral", "bm-amount-neutral"

                def income_amount_class(total_cents: int) -> str:
                    if total_cents > 0:
                        return "bm-amount-income"
                    if total_cents < 0:
                        return "bm-amount-expense"
                    return "bm-amount-neutral"

                def render_subcategories(current_from: date, current_to: date) -> None:
                    subcategories_container.clear()
                    with subcategories_container:
                        if not selected_cost_type_id:
                            with ui.card().classes("bm-card p-3"):
                                ui.label("Für den Drilldown bitte eine Kostenkategorie auswählen.")
                            return

                        rows_raw = services.report_service.build_subcategory_breakdown(
                            date_from=current_from,
                            date_to=current_to,
                            cost_type_id=selected_cost_type_id,
                        )
                        title = selected_cost_type_name or "Ausgewählte Kostenkategorie"
                        ui.label(f"Unterkategorien zu: {title}").classes("text-base font-semibold bm-report-title")
                        if not rows_raw:
                            with ui.card().classes("bm-card p-3"):
                                ui.label("Keine Unterkategorien im Zeitraum.")
                            return

                        rows = []
                        for item in rows_raw:
                            _, direction_color = amount_state(item.total_cents)
                            rows.append(
                                {
                                    "id": item.cost_subcategory_id,
                                    "name": item.cost_subcategory_name,
                                    "total": _format_cents(item.total_cents, settings.default_currency),
                                    "total_class": direction_color,
                                    "mobile_title": item.cost_subcategory_name,
                                    "mobile_primary_left": "Unterkategorie",
                                    "mobile_primary_right": _format_cents(item.total_cents, settings.default_currency),
                                    "mobile_secondary": "Summiert im gewählten Zeitraum",
                                }
                            )
                        columns = [
                            {"name": "name", "label": "Unterkategorie", "field": "name", "align": "left", "sortable": True},
                            {
                                "name": "total",
                                "label": f"Summe ({settings.default_currency})",
                                "field": "total",
                                "align": "right",
                                "sortable": True,
                            },
                        ]
                        table = _responsive_erp_table(columns=columns, rows=rows, row_key="id", rows_per_page=20)
                        table.add_slot(
                            "body-cell-total",
                            """
                            <q-td :props="props" class="text-right">
                              <span :class="props.row.total_class">{{ props.row.total }}</span>
                            </q-td>
                            """,
                        )

                def render_income_orders(current_from: date, current_to: date) -> None:
                    income_orders_container.clear()
                    with income_orders_container:
                        if not selected_income_project_id:
                            with ui.card().classes("bm-card p-3"):
                                ui.label("Für den Einnahmen-Drilldown bitte ein Projekt auswählen.")
                            return

                        rows_raw = services.report_service.build_income_order_breakdown(
                            date_from=current_from,
                            date_to=current_to,
                            project_id=selected_income_project_id,
                        )
                        title = selected_income_project_name or "Ausgewähltes Projekt"
                        ui.label(f"Verkäufe mit Rechnungsdatum zu: {title}").classes("text-base font-semibold bm-report-title")
                        if not rows_raw:
                            with ui.card().classes("bm-card p-3"):
                                ui.label("Keine Verkäufe mit Rechnungsdatum im Zeitraum.")
                            return

                        rows = [
                            {
                                "id": item.order_id,
                                "internal_number": item.internal_number,
                                "contact": item.contact_name,
                                "invoice_date": item.invoice_date.isoformat(),
                                "total": _format_cents(item.total_cents, settings.default_currency),
                                "total_class": income_amount_class(item.total_cents),
                                "mobile_title": item.internal_number,
                                "mobile_primary_left": item.contact_name,
                                "mobile_primary_right": _format_cents(item.total_cents, settings.default_currency),
                                "mobile_secondary": f"Rechnungsdatum {item.invoice_date.isoformat()}",
                            }
                            for item in rows_raw
                        ]
                        columns = [
                            {"name": "internal_number", "label": "Verkaufsnummer", "field": "internal_number", "align": "left"},
                            {"name": "contact", "label": "Kontakt", "field": "contact", "align": "left"},
                            {"name": "invoice_date", "label": "Rechnungsdatum", "field": "invoice_date", "align": "left"},
                            {
                                "name": "total",
                                "label": f"Projektanteil ({settings.default_currency})",
                                "field": "total",
                                "align": "right",
                            },
                        ]
                        table = _responsive_erp_table(columns=columns, rows=rows, row_key="id", rows_per_page=20)
                        table.add_slot(
                            "body-cell-total",
                            """
                            <q-td :props="props" class="text-right">
                              <span :class="props.row.total_class">{{ props.row.total }}</span>
                            </q-td>
                            """,
                        )
                        table.on("rowClick", lambda event: ui.navigate.to(f"/verkaeufe/{_extract_row_id(event) or -1}"))

                def render_income_report(current_from: date, current_to: date) -> None:
                    nonlocal selected_income_project_id, selected_income_project_name
                    income_summary_container.clear()
                    income_projects_container.clear()
                    income_orders_container.clear()

                    income_report = services.report_service.build_income_summary(current_from, current_to)
                    with income_summary_container:
                        with ui.card().classes("bm-card p-3"):
                            ui.label("Verkäufe mit Rechnungsdatum").classes("text-sm font-semibold")
                            ui.label(_format_cents(income_report.overall_total_cents, settings.default_currency)).classes(
                                f"text-2xl font-bold {income_amount_class(income_report.overall_total_cents)}"
                            )
                            ui.label(
                                f"Auswertung nach Rechnungsdatum · {income_report.order_count} Verkäufe"
                            ).classes("text-xs text-slate-600")

                    with income_projects_container:
                        ui.label("Einnahmen je Projekt").classes("text-base font-semibold bm-report-title")
                        if not income_report.totals_by_project:
                            with ui.card().classes("bm-card p-3"):
                                ui.label("Keine abgerechneten Verkäufe im Zeitraum.")
                            selected_income_project_id = None
                            selected_income_project_name = None
                            return

                        project_name_by_id = {
                            item.project_id: item.project_name for item in income_report.totals_by_project
                        }
                        if selected_income_project_id not in project_name_by_id:
                            selected_income_project_id = income_report.totals_by_project[0].project_id
                            selected_income_project_name = income_report.totals_by_project[0].project_name

                        rows = [
                            {
                                "id": item.project_id,
                                "name": item.project_name,
                                "total": _format_cents(item.total_cents, settings.default_currency),
                                "total_class": income_amount_class(item.total_cents),
                                "mobile_title": item.project_name,
                                "mobile_primary_left": "Projekt",
                                "mobile_primary_right": _format_cents(item.total_cents, settings.default_currency),
                                "mobile_secondary": "Einnahmen im gewählten Zeitraum",
                            }
                            for item in income_report.totals_by_project
                        ]
                        columns = [
                            {"name": "name", "label": "Projekt", "field": "name", "align": "left", "sortable": True},
                            {
                                "name": "total",
                                "label": f"Summe ({settings.default_currency})",
                                "field": "total",
                                "align": "right",
                                "sortable": True,
                            },
                        ]
                        table = _responsive_erp_table(columns=columns, rows=rows, row_key="id", rows_per_page=20)
                        table.add_slot(
                            "body-cell-total",
                            """
                            <q-td :props="props" class="text-right">
                              <span :class="props.row.total_class">{{ props.row.total }}</span>
                            </q-td>
                            """,
                        )

                        def on_income_project_click(event: events.GenericEventArguments) -> None:
                            nonlocal selected_income_project_id, selected_income_project_name
                            project_id_raw = _extract_row_id(event)
                            if project_id_raw is None:
                                return
                            project_id = int(project_id_raw)
                            if project_id < 0 or project_id not in project_name_by_id:
                                return
                            selected_income_project_id = project_id
                            selected_income_project_name = project_name_by_id[project_id]
                            render_income_orders(current_from, current_to)

                        table.on("rowClick", on_income_project_click)
                        render_income_orders(current_from, current_to)

                def render_report() -> None:
                    nonlocal selected_cost_type_id, selected_cost_type_name
                    summary_container.clear()
                    categories_container.clear()
                    income_summary_container.clear()
                    income_projects_container.clear()
                    income_orders_container.clear()

                    from_value = _parse_iso_date(str(date_from.value or ""))
                    to_value = _parse_iso_date(str(date_to.value or ""))

                    if from_value and to_value:
                        render_income_report(from_value, to_value)

                    with summary_container:
                        if not from_value or not to_value:
                            with ui.card().classes("bm-card p-3"):
                                ui.label("Bitte Startdatum und Enddatum eingeben.")
                            subcategories_container.clear()
                            income_orders_container.clear()
                            return
                        if from_value > to_value:
                            with ui.card().classes("bm-card p-3"):
                                ui.label("Startdatum darf nicht nach dem Enddatum liegen.")
                            subcategories_container.clear()
                            income_orders_container.clear()
                            return

                        report = services.report_service.build_summary(from_value, to_value)
                        total_label, total_color = amount_state(report.overall_total_cents)
                        with ui.card().classes("bm-card p-3"):
                            ui.label("Gesamtsumme aller Belege").classes("text-sm font-semibold")
                            ui.label(_format_cents(report.overall_total_cents, settings.default_currency)).classes(
                                f"text-2xl font-bold {total_color}"
                            )
                            ui.label(f"{total_label} im Zeitraum · {report.receipt_count} auswertbare Belege").classes(
                                "text-xs text-slate-600"
                            )

                    with categories_container:
                        ui.label("Gesamtausgaben je Kategorie").classes("text-base font-semibold bm-report-title")
                        if not from_value or not to_value:
                            return
                        report = services.report_service.build_summary(from_value, to_value)
                        if not report.totals_by_cost_type:
                            with ui.card().classes("bm-card p-3"):
                                ui.label("Keine auswertbaren Belege im Zeitraum.")
                            subcategories_container.clear()
                            selected_cost_type_id = None
                            selected_cost_type_name = None
                        else:
                            category_name_by_id = {
                                item.cost_type_id: item.cost_type_name for item in report.totals_by_cost_type
                            }
                            if selected_cost_type_id not in category_name_by_id:
                                selected_cost_type_id = report.totals_by_cost_type[0].cost_type_id
                                selected_cost_type_name = report.totals_by_cost_type[0].cost_type_name

                            rows = []
                            for item in report.totals_by_cost_type:
                                _, direction_color = amount_state(item.total_cents)
                                rows.append(
                                    {
                                        "id": item.cost_type_id,
                                        "name": item.cost_type_name,
                                        "total": _format_cents(item.total_cents, settings.default_currency),
                                        "total_class": direction_color,
                                        "mobile_title": item.cost_type_name,
                                        "mobile_primary_left": "Kostenkategorie",
                                        "mobile_primary_right": _format_cents(item.total_cents, settings.default_currency),
                                        "mobile_secondary": "Ausgaben im gewählten Zeitraum",
                                    }
                                )
                            columns = [
                                {
                                    "name": "name",
                                    "label": "Kostenkategorie",
                                    "field": "name",
                                    "align": "left",
                                    "sortable": True,
                                },
                                {
                                    "name": "total",
                                    "label": f"Summe ({settings.default_currency})",
                                    "field": "total",
                                    "align": "right",
                                    "sortable": True,
                                },
                            ]
                            table = _responsive_erp_table(columns=columns, rows=rows, row_key="id", rows_per_page=20)
                            table.add_slot(
                                "body-cell-total",
                                """
                                <q-td :props="props" class="text-right">
                                  <span :class="props.row.total_class">{{ props.row.total }}</span>
                                </q-td>
                                """,
                            )

                            def on_category_click(event: events.GenericEventArguments) -> None:
                                nonlocal selected_cost_type_id, selected_cost_type_name
                                category_id = _extract_row_id(event) or -1
                                if category_id <= 0:
                                    return
                                if category_id not in category_name_by_id:
                                    return
                                selected_cost_type_id = category_id
                                selected_cost_type_name = category_name_by_id[category_id]
                                render_subcategories(from_value, to_value)

                            table.on("rowClick", on_category_click)
                            render_subcategories(from_value, to_value)

                render_report()

    @ui.page("/kostenbereiche")
    def cost_areas_page() -> None:
        with _shell("/kostenbereiche", "Kostenstellen"):
            with ui.card().classes("bm-card p-4 w-full"):
                ui.label("Kostenstellen-Verwaltung ist aktuell deaktiviert.").classes("text-lg font-semibold")
                ui.label(
                    "Die Zuordnung zur Standard-Kostenstelle läuft automatisch im Hintergrund, wenn kein Projekt gewählt ist."
                ).classes("text-sm text-slate-600")
                ui.button("Zurück zu Belegen", icon="arrow_back", on_click=lambda: ui.navigate.to("/belege")).props(
                    "flat"
                )

    @ui.page("/einstellungen")
    def settings_page() -> None:
        from ..config import settings
        from ..legal import APP_COPYRIGHT, APP_LICENSE_ID, ThirdPartyNotice, get_third_party_notices
        from ..versioning import get_app_version

        with _shell("/einstellungen", "Einstellungen"):
            invoice_profile = services.invoice_service.get_profile()
            logo_state: dict[str, str | None] = {"path": invoice_profile.logo_path}
            logo_preview_container: Any = None
            logo_actions_container: Any = None
            logo_hint_label: Any = None
            invoice_settings_expanded = {"value": False}
            invoice_settings_toggle_btn: Any = None
            invoice_settings_panel: Any = None

            def current_logo_url() -> str | None:
                return to_files_url(logo_state.get("path"))

            def open_logo() -> None:
                logo_url = current_logo_url()
                if not logo_url:
                    ui.notify("Kein Logo hinterlegt", type="warning")
                    return
                open_in_new_tab(logo_url)

            async def handle_logo_upload(event: events.MultiUploadEventArguments) -> None:
                if not event.files:
                    return
                file_upload = event.files[0]
                new_logo_path: Path | None = None
                try:
                    new_logo_path = await save_uploaded_invoice_logo(file_upload)
                    previous_logo_path = logo_state.get("path")
                    services.invoice_service.set_logo_path(str(new_logo_path))
                    if previous_logo_path and previous_logo_path != str(new_logo_path):
                        safe_delete_file(previous_logo_path)
                    logo_state["path"] = str(new_logo_path)
                    render_logo_controls()
                    ui.notify("Logo gespeichert", type="positive")
                except Exception as exc:
                    if new_logo_path is not None:
                        safe_delete_file(new_logo_path)
                    _notify_error("Logo konnte nicht gespeichert werden", exc)

            def remove_logo() -> None:
                try:
                    old_logo_path = services.invoice_service.clear_logo_path()
                    safe_delete_file(old_logo_path)
                    logo_state["path"] = None
                    render_logo_controls()
                    ui.notify("Logo entfernt", type="positive")
                except Exception as exc:
                    _notify_error("Logo konnte nicht entfernt werden", exc)

            def render_logo_controls() -> None:
                logo_preview_container.clear()
                with logo_preview_container:
                    logo_url = current_logo_url()
                    if logo_url:
                        ui.image(logo_url).classes("max-w-[260px] max-h-24 object-contain self-start")
                    else:
                        ui.label("Noch kein Logo hinterlegt.").classes("text-sm text-slate-600")

                logo_actions_container.clear()
                with logo_actions_container:
                    if current_logo_url():
                        view_logo_btn = ui.button(icon="visibility", on_click=open_logo).props("flat round dense")
                        view_logo_btn.tooltip("Logo anzeigen")
                    logo_upload = ui.upload(
                        multiple=False,
                        auto_upload=True,
                        on_multi_upload=handle_logo_upload,
                        label="",
                    ).classes("bm-hidden-upload")
                    logo_upload.props("accept=.jpg,.jpeg,.png,.heic,.heif")
                    upload_logo_btn = ui.button(
                        icon="upload_file",
                        on_click=lambda: logo_upload.run_method("pickFiles"),
                    ).props("flat round dense")
                    upload_logo_btn.tooltip("Logo hochladen oder ersetzen")
                    if current_logo_url():
                        remove_logo_btn = ui.button(icon="delete_outline", on_click=remove_logo).props(
                            "flat round dense color=negative"
                        )
                        remove_logo_btn.tooltip("Logo entfernen")

                logo_hint_label.text = "Empfohlen: PNG mit transparentem Hintergrund mit ca. 1000 x 300 px."

            def render_invoice_settings_visibility() -> None:
                expanded = bool(invoice_settings_expanded["value"])
                invoice_settings_panel.set_visibility(expanded)
                invoice_settings_toggle_btn.text = (
                    "Eigene Rechnungsdaten ausblenden" if expanded else "Eigene Rechnungsdaten bearbeiten"
                )
                invoice_settings_toggle_btn.icon = "expand_less" if expanded else "edit"

            def open_change_password_dialog() -> None:
                with ui.dialog() as dialog, ui.card().classes("bm-card p-5 w-[520px] max-w-full gap-4"):
                    ui.label("Kennwort ändern").classes("text-lg font-semibold")
                    ui.label(
                        "Nach einer erfolgreichen Änderung werden bestehende Sitzungen beendet und du meldest dich neu an."
                    ).classes("text-sm text-slate-600")

                    current_password_input = ui.input("Aktuelles Passwort").props("type=password").classes("w-full bm-form-field")
                    new_password_input = ui.input("Neues Passwort").props("type=password").classes("w-full bm-form-field")
                    confirm_password_input = ui.input("Neues Passwort wiederholen").props("type=password").classes(
                        "w-full bm-form-field"
                    )

                    async def submit_change_password() -> None:
                        await _flush_active_input(context.client)
                        if (new_password_input.value or "") != (confirm_password_input.value or ""):
                            ui.notify("Die neuen Passwörter stimmen nicht überein", type="negative")
                            return

                        current_user = services.auth_service.session_user(context.client.request)
                        if current_user is None or current_user.id is None:
                            dialog.close()
                            _run_client_javascript(context.client, "window.location.assign('/login')")
                            return

                        try:
                            services.auth_service.change_password(
                                user_id=int(current_user.id),
                                current_password=current_password_input.value or "",
                                new_password=new_password_input.value or "",
                            )
                        except Exception as exc:
                            _notify_error("Kennwort konnte nicht geändert werden", exc)
                            return

                        dialog.close()
                        ui.notify("Kennwort geändert. Bitte neu anmelden.", type="positive")
                        await asyncio.sleep(0.2)
                        _run_client_javascript(context.client, "window.location.assign('/login')")

                    with ui.row().classes("w-full justify-end gap-2"):
                        ui.button("Abbrechen", on_click=dialog.close).props("flat")
                        ui.button("Kennwort ändern", icon="lock_reset", on_click=submit_change_password).props(
                            "color=primary"
                        )

                dialog.open()

            with ui.card().classes("bm-card p-4 w-full gap-3"):
                ui.label("Zugang & Sicherheit").classes("text-lg font-semibold")
                ui.label(
                    "Wenn du dein aktuelles Kennwort kennst, kannst du es hier direkt ändern."
                ).classes("text-sm text-slate-600")
                with ui.row().classes("w-full justify-end"):
                    ui.button("Kennwort ändern", icon="password", on_click=open_change_password_dialog).props(
                        "outline no-caps"
                    )

            with ui.card().classes("bm-card p-4 w-full gap-4"):
                ui.label("Rechnungssteller & Rechnung").classes("text-lg font-semibold")
                ui.label(
                    "Diese Daten werden installweit fuer automatisch erzeugte Rechnungen verwendet."
                ).classes("text-sm text-slate-600")
                invoice_settings_toggle_btn = ui.button("").props("outline no-caps").classes("self-start")
                invoice_settings_toggle_btn.on(
                    "click",
                    lambda _: (
                        invoice_settings_expanded.__setitem__("value", not invoice_settings_expanded["value"]),
                        render_invoice_settings_visibility(),
                    ),
                )

                with ui.column().classes("w-full gap-4") as invoice_settings_panel:
                    with ui.row().classes("w-full gap-3 wrap bm-form-row"):
                        display_name_input = ui.input("Anzeigename / Firma", value=invoice_profile.display_name or "").classes(
                            "flex-1 min-w-[280px] bm-form-field"
                        )
                        payment_term_days_input = ui.input(
                            "Standard-Zahlungsziel (Tage)",
                            value="" if invoice_profile.payment_term_days is None else str(invoice_profile.payment_term_days),
                        ).props("type=number min=1 max=365").classes("w-56 bm-form-field")

                    with ui.card().classes("bm-card p-4 w-full gap-3"):
                        ui.label("Adresse").classes("text-base font-semibold")
                        with ui.row().classes("w-full gap-3 wrap bm-form-row"):
                            street_input = ui.input("Straße", value=invoice_profile.street or "").classes(
                                "flex-1 min-w-[240px] bm-form-field"
                            )
                            house_number_input = ui.input("Hausnummer", value=invoice_profile.house_number or "").classes(
                                "w-36 bm-form-field"
                            )
                        with ui.row().classes("w-full gap-3 wrap bm-form-row"):
                            address_extra_input = ui.input("Adresszusatz", value=invoice_profile.address_extra or "").classes(
                                "w-full bm-form-field"
                            )
                        with ui.row().classes("w-full gap-3 wrap bm-form-row"):
                            postal_code_input = ui.input("PLZ", value=invoice_profile.postal_code or "").classes("w-40 bm-form-field")
                            city_input = ui.input("Ort", value=invoice_profile.city or "").classes(
                                "flex-1 min-w-[220px] bm-form-field"
                            )
                        country_input = ui.select(
                            country_options(),
                            value=(invoice_profile.country or DEFAULT_CONTACT_COUNTRY_CODE).strip().upper(),
                            label="Land",
                        ).props("use-input input-debounce=0").classes("w-full bm-form-field")

                    with ui.card().classes("bm-card p-4 w-full gap-3"):
                        ui.label("Kontakt & Steuer").classes("text-base font-semibold")
                        with ui.row().classes("w-full gap-3 wrap bm-form-row"):
                            email_input = ui.input("E-Mail (optional)", value=invoice_profile.email or "").classes(
                                "flex-1 min-w-[220px] bm-form-field"
                            )
                            phone_input = ui.input("Telefon (optional)", value=invoice_profile.phone or "").classes(
                                "flex-1 min-w-[220px] bm-form-field"
                            )
                        website_input = ui.input("Website (optional)", value=invoice_profile.website or "").classes(
                            "w-full bm-form-field"
                        )
                        tax_id_type_input = ui.toggle(
                            {"tax_number": "Steuernummer", "vat_id": "USt-IdNr."},
                            value=(invoice_profile.tax_id_type or "tax_number").strip().lower(),
                        ).props("unelevated no-caps").classes("bm-view-mode-btn bm-doc-type-toggle bm-form-field")
                        tax_id_value_input = ui.input("Kennzeichen", value=invoice_profile.tax_id_value or "").classes(
                            "w-full bm-form-field"
                        )

                    with ui.card().classes("bm-card p-4 w-full gap-3"):
                        ui.label("Bankverbindung & Logo").classes("text-base font-semibold")
                        with ui.row().classes("w-full gap-3 wrap bm-form-row"):
                            bank_account_holder_input = ui.input(
                                "Kontoinhaber",
                                value=invoice_profile.bank_account_holder or "",
                            ).classes("flex-1 min-w-[220px] bm-form-field")
                            iban_input = ui.input("IBAN", value=invoice_profile.iban or "").classes(
                                "flex-1 min-w-[220px] bm-form-field"
                            )
                        with ui.row().classes("w-full gap-3 wrap bm-form-row"):
                            bic_input = ui.input("BIC", value=invoice_profile.bic or "").classes("w-56 bm-form-field")
                        with ui.column().classes("w-full gap-2"):
                            ui.label("Logo").classes("text-sm font-semibold")
                            with ui.row().classes("w-full items-start justify-between gap-3 wrap"):
                                logo_preview_container = ui.column().classes("min-w-[260px] flex-[1_1_280px] gap-2")
                                logo_actions_container = ui.row().classes("items-center gap-1")
                            logo_hint_label = ui.label("").classes("text-xs text-slate-600")
                            render_logo_controls()

                async def save_invoice_profile() -> None:
                    await _flush_active_input(context.client)
                    try:
                        services.invoice_service.update_profile(
                            display_name=display_name_input.value,
                            street=street_input.value,
                            house_number=house_number_input.value,
                            address_extra=address_extra_input.value,
                            postal_code=postal_code_input.value,
                            city=city_input.value,
                            country=country_input.value,
                            email=email_input.value,
                            phone=phone_input.value,
                            website=website_input.value,
                            tax_id_type=tax_id_type_input.value,
                            tax_id_value=tax_id_value_input.value,
                            bank_account_holder=bank_account_holder_input.value,
                            iban=iban_input.value,
                            bic=bic_input.value,
                            payment_term_days=payment_term_days_input.value,
                        )
                    except Exception as exc:
                        _notify_error("Rechnungssteller konnte nicht gespeichert werden", exc)
                        return
                    ui.notify("Rechnungssteller gespeichert", type="positive")

                with ui.row().classes("w-full justify-end"):
                    ui.button("Rechnungssteller speichern", icon="save", on_click=save_invoice_profile).props(
                        "color=primary"
                    )
                render_invoice_settings_visibility()

            with ui.card().classes("bm-card p-4 w-full"):
                ui.label("Version & Rechtliches").classes("text-lg font-semibold")
                ui.label(f"App-Version: {get_app_version()}")
                ui.label(APP_COPYRIGHT)
                ui.label(f"Lizenz: {APP_LICENSE_ID}")

                with ui.card().classes("bm-card p-3 w-full max-w-lg gap-1"):
                    ui.label("Debug").classes("text-sm font-semibold")
                    with ui.row().classes("w-full items-center justify-between"):
                        ui.label("Queue/Pending").classes("text-xs text-slate-600")
                        debug_queue_value = ui.label("0").classes("text-sm font-semibold")
                    with ui.row().classes("w-full items-center justify-between"):
                        ui.label("Fehler").classes("text-xs text-slate-600")
                        debug_error_value = ui.label("0").classes("text-sm font-semibold")

                def refresh_debug_values() -> None:
                    with Session(engine) as session:
                        queued = len(
                            session.exec(
                                select(Receipt.id).where(Receipt.status == "queued", Receipt.deleted_at.is_(None))
                            ).all()
                        )
                        running = len(
                            session.exec(
                                select(Receipt.id).where(Receipt.status == "running", Receipt.deleted_at.is_(None))
                            ).all()
                        )
                        errors = len(
                            session.exec(
                                select(Receipt.id).where(Receipt.status == "error", Receipt.deleted_at.is_(None))
                            ).all()
                        )
                    debug_queue_value.text = str(queued + running + services.job_queue.pending_count())
                    debug_error_value.text = str(errors)

                refresh_debug_values()
                ui.timer(5.0, refresh_debug_values)

                def open_third_party_notices() -> None:
                    notices_state: dict[str, Any] = {
                        "items": get_third_party_notices(force_refresh=False),
                        "selected_row_id": 1,
                    }
                    summary_container: ui.column
                    details_container: ui.column

                    def _selected_notice() -> ThirdPartyNotice | None:
                        selected_id = int(notices_state.get("selected_row_id") or 0)
                        for idx, notice in enumerate(notices_state["items"], start=1):
                            if idx == selected_id:
                                return notice
                        return notices_state["items"][0] if notices_state["items"] else None

                    def render_details() -> None:
                        details_container.clear()
                        with details_container:
                            notice = _selected_notice()
                            if not notice:
                                with ui.card().classes("bm-card p-3 w-full"):
                                    ui.label("Keine Fremdlizenzen gefunden.").classes("text-sm")
                                return
                            with ui.card().classes("bm-card p-3 w-full gap-2"):
                                ui.label(f"Details: {notice.name} {notice.version}").classes("text-sm font-semibold")
                                ui.label(f"Lizenz: {notice.license}").classes("text-sm")
                                if notice.homepage:
                                    ui.link(notice.homepage, notice.homepage, new_tab=True).classes("text-xs")
                                else:
                                    ui.label("Quelle: -").classes("text-xs text-slate-600")
                                if not notice.license_files:
                                    ui.label("Keine Lizenzdatei im Paket gefunden.").classes("text-xs text-slate-600")
                                    return
                                for license_file in notice.license_files:
                                    with ui.expansion(f"Lizenzdatei: {license_file.path}").classes("w-full"):
                                        ui.label(license_file.text).classes(
                                            "text-xs whitespace-pre-wrap break-words max-h-56 overflow-auto"
                                        )

                    def render_summary() -> None:
                        summary_container.clear()
                        with summary_container:
                            notices = notices_state["items"]
                            ui.label(f"Gefundene Python-Pakete: {len(notices)}").classes("text-xs text-slate-600")
                            if not notices:
                                with ui.card().classes("bm-card p-3 w-full"):
                                    ui.label("Keine Lizenzinformationen gefunden.").classes("text-sm")
                                return

                            rows = [
                                {
                                    "id": idx,
                                    "name": item.name,
                                    "version": item.version,
                                    "license": item.license,
                                    "source_label": "Öffnen" if item.homepage else "-",
                                    "source_url": item.homepage or "",
                                    "mobile_title": item.name,
                                    "mobile_primary_left": f"Version {item.version}",
                                    "mobile_primary_right": item.license,
                                    "mobile_secondary": item.homepage or "Keine Quelle hinterlegt",
                                }
                                for idx, item in enumerate(notices, start=1)
                            ]
                            columns = [
                                {"name": "name", "label": "Paket", "field": "name", "align": "left", "sortable": True},
                                {"name": "version", "label": "Version", "field": "version", "align": "left", "sortable": True},
                                {"name": "license", "label": "Lizenz", "field": "license", "align": "left", "sortable": True},
                                {"name": "source_label", "label": "Quelle", "field": "source_label", "align": "left"},
                            ]
                            table = _responsive_erp_table(columns=columns, rows=rows, row_key="id", rows_per_page=15).classes(
                                "bm-licenses-table"
                            )
                            table.add_slot(
                                "body-cell-source_label",
                                """
                                <q-td :props="props">
                                  <a
                                    v-if="props.row.source_url"
                                    :href="props.row.source_url"
                                    target="_blank"
                                    rel="noopener noreferrer"
                                  >
                                    {{ props.row.source_label }}
                                  </a>
                                  <span v-else>-</span>
                                </q-td>
                                """,
                            )

                            def on_row_click(event: events.GenericEventArguments) -> None:
                                row_id = _extract_row_id(event)
                                if not row_id:
                                    return
                                notices_state["selected_row_id"] = row_id
                                render_details()

                            table.on("rowClick", on_row_click)

                    def refresh_notices() -> None:
                        notices_state["items"] = get_third_party_notices(force_refresh=True)
                        notices_state["selected_row_id"] = 1
                        render_summary()
                        render_details()
                        ui.notify("Fremdlizenzen aktualisiert", type="positive")

                    with ui.dialog().classes("w-full") as dialog, ui.card().classes("bm-card bm-licenses-dialog p-4"):
                        with ui.row().classes("w-full items-center justify-between"):
                            ui.label("Fremdlizenzen").classes("text-lg font-semibold")
                            ui.button(icon="close", on_click=dialog.close).props("flat round dense")
                        ui.label(
                            "Hinweis: Diese Liste erfasst Python-Abhängigkeiten inklusive transitiver Pakete. "
                            "Systemtools wie ocrmypdf, tesseract und ghostscript sind separat installiert."
                        ).classes("text-xs text-slate-600")
                        with ui.row().classes("w-full justify-end"):
                            ui.button("Liste aktualisieren", icon="refresh", on_click=refresh_notices).props("flat no-caps")
                        with ui.column().classes("bm-licenses-content"):
                            summary_container = ui.column().classes("w-full min-w-0 gap-2")
                            details_container = ui.column().classes("w-full min-w-0 gap-2")
                        render_summary()
                        render_details()

                    dialog.open()

                ui.button("Fremdlizenzen", icon="policy", on_click=open_third_party_notices).props("flat no-caps")

                ui.label("Lokales Setup").classes("text-lg font-semibold mt-2")
                ui.label(f"Datenbank: {settings.db_path}")
                ui.label(f"Archiv: {settings.archive_dir}")
                ui.label(f"Rechnungsassets: {settings.invoice_assets_dir}")
                ui.label(f"Projekt-Cover: {settings.works_cover_dir}")
                ui.label(f"OCR-Sprachen: {settings.ocr_languages}")
                ui.label(f"Währung (Default): {settings.default_currency}")
                ui.label(f"USt-Satz (Default): {settings.default_vat_rate_percent:.2f}%")
                ui.label("Login aktiviert (Session mit Timeout + einfache Ersteinrichtung für den ersten Admin).").classes(
                    "text-sm text-slate-600"
                )
