from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable

from nicegui import events, ui
from sqlalchemy import delete, func, or_, update as sa_update
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from ..app_state import ServiceContainer
from ..config import settings
from ..constants import (
    COST_TYPE_ICON_OPTIONS,
    DEFAULT_SUBCATEGORY_NAME,
    DEFAULT_COST_TYPE_ICON,
    default_subcategory_name_for_cost_type,
)
from ..db import engine
from ..models import CostAllocation, CostSubcategory, CostType, Project, Receipt, Supplier
from ..schemas import AllocationInput
from ..utils.storage import is_supported_filename, save_uploaded_work_cover, safe_delete_file, to_files_url

DEFAULT_PROJECT_COLOR = "#5c30ff"
DOC_TYPE_INVOICE = "invoice"
DOC_TYPE_CREDIT_NOTE = "credit_note"
_NAV_STATE = {
    "sidebar_expanded": True,
    "open_groups": {"management": True},
    "last_path": None,
}

_NAV_CONFIG: list[dict[str, Any]] = [
    {"type": "item", "path": "/", "label": "Dashboard", "icon": "dashboard"},
    {"type": "item", "path": "/belege", "label": "Belege", "icon": "description"},
    {"type": "item", "path": "/projekte", "label": "Projekte", "icon": "palette"},
    {
        "type": "group",
        "key": "management",
        "label": "Verwaltung",
        "icon": "admin_panel_settings",
        "items": [
            {"path": "/lieferanten", "label": "Anbieter", "icon": "local_shipping"},
            {"path": "/kategorien", "label": "Kostenkategorien", "icon": "category"},
        ],
    },
    {"type": "item", "path": "/auswertung", "label": "Auswertung", "icon": "insights"},
    {"type": "item", "path": "/einstellungen", "label": "Einstellungen", "icon": "settings"},
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
    "/projekte": {
        "title": "Hilfe · Projekte",
        "body": "Hier verwaltest du deine Werke, Ausstellungen und Aufträge. Projekte sind "
        "konkrete Vorhaben mit klarem Bezug. Wenn kein Projekt gewählt wird, läuft die "
        "Zuordnung automatisch als allgemeine Ausgabe.",
    },
    "/lieferanten": {
        "title": "Hilfe · Anbieter",
        "body": "Hier sammelst du Firmen, Shops, Dienstleister und Vermieter, von denen deine "
        "Belege kommen. Das spart Tipparbeit und sorgt dafür, dass wiederkehrende Angaben "
        "nicht jedes Mal neu zusammengesucht werden müssen.",
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
        "body": "Hier findest du technische Informationen zu deinem lokalen Setup wie Version, "
        "Datenbankpfade und OCR-Standardwerte.",
    },
}

_DEFAULT_HELP_CONTENT = {
    "title": "Hilfe",
    "body": "Hier findest du Kontext zur aktuellen Seite. Weitere Hilfefunktionen können später ergänzt werden.",
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


def _erp_table(
    *,
    columns: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    row_key: str = "id",
    rows_per_page: int = 25,
) -> ui.table:
    table = ui.table(
        columns=columns,
        rows=rows,
        row_key=row_key,
        pagination={"rowsPerPage": rows_per_page},
    ).classes("w-full bm-card bm-table")
    table.props("flat dense wrap-cells separator=horizontal")
    return table


def _icon_option_html(label: str, icon: str) -> str:
    return (
        "<span style='display:inline-flex;align-items:center;gap:8px;'>"
        f"<span class='material-icons' style='font-size:18px;line-height:1'>{icon}</span>"
        f"<span>{label}</span>"
        "</span>"
    )


@contextmanager
def _shell(active_path: str, title: str):
    def context_class(path: str) -> str:
        if path.startswith("/belege") or path.startswith("/lieferanten") or path.startswith("/kategorien") or path.startswith(
            "/import"
        ):
            return "bm-context-expenses"
        if path.startswith("/projekte"):
            return "bm-context-works"
        if path.startswith("/auswertung"):
            return "bm-context-reports"
        if path.startswith("/einstellungen"):
            return "bm-context-settings"
        return "bm-context-dashboard"

    is_expanded = bool(_NAV_STATE["sidebar_expanded"])

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

    def toggle_sidebar() -> None:
        _NAV_STATE["sidebar_expanded"] = not bool(_NAV_STATE["sidebar_expanded"])
        ui.navigate.to(active_path)

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
        items = list(group_entry.get("items") or [])
        first_path = str(items[0].get("path") or "") if items else ""
        is_open = bool(next_open_groups.get(group_key))
        if not is_open:
            next_open_groups[group_key] = True
            _NAV_STATE["open_groups"] = next_open_groups
            if first_path:
                ui.navigate.to(first_path)
            else:
                ui.navigate.to(active_path)
            return
        next_open_groups[group_key] = False
        _NAV_STATE["open_groups"] = next_open_groups
        ui.navigate.to(active_path)

    def nav_item(path: str, label: str, icon: str, *, nested: bool = False) -> None:
        active = active_path == path
        button_label = label if is_expanded else ""
        classes = "bm-nav-item w-full"
        if active:
            classes += " bm-nav-item--active"
        if nested and is_expanded:
            classes += " bm-nav-item--nested"
        if nested and not is_expanded:
            classes += " bm-nav-item--nested-mini"
        if not is_expanded:
            classes += " bm-nav-item--mini"
        button_props = "flat no-caps align=left"
        if not is_expanded:
            button_props = "flat no-caps"
        ui.button(
            button_label,
            icon=icon,
            on_click=lambda p=path: ui.navigate.to(p),
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
        with ui.row().classes("bm-global-header w-full items-center"):
            with ui.row().classes("bm-global-header-inner w-full items-center justify-between"):
                with ui.row().classes("items-center gap-2"):
                    ui.image("/assets/hamster-logo.svg").classes("bm-global-brand-logo")
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
                        on_click=lambda: ui.navigate.to("/einstellungen"),
                    ).props("flat round dense").classes("bm-global-icon-btn")

        with ui.row().classes("bm-app-shell w-full"):
            sidebar_classes = "bm-sidebar"
            if not is_expanded:
                sidebar_classes += " bm-sidebar--mini"

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
                    if is_expanded:
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
                with ui.row().classes("bm-page-head w-full items-center justify-between"):
                    with ui.column().classes("gap-1"):
                        ui.label(title).classes("bm-page-title")
                    if is_expanded:
                        ui.image("/assets/blob-bg.svg").classes("w-24 opacity-70")
                with ui.column().classes("w-full max-w-7xl mx-auto gap-4"):
                    yield


def register_pages(services: ServiceContainer) -> None:
    def project_options(active_only: bool = True) -> dict[int, str]:
        with Session(engine) as session:
            stmt = select(Project).order_by(Project.name)
            if active_only:
                stmt = stmt.where(Project.active.is_(True))
            projects = list(session.exec(stmt).all())
        return {project.id: project.name for project in projects if project.id is not None}

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

    def missing_required_fields(receipt: Receipt) -> list[str]:
        missing: list[str] = []
        if receipt.doc_date is None:
            missing.append("Belegdatum")
        if receipt.supplier_id is None:
            missing.append("Anbieter")
        if receipt.amount_gross_cents is None:
            missing.append(f"Brutto ({settings.default_currency})")
        if receipt.vat_rate_percent is None:
            missing.append("USt-Satz")
        if receipt.amount_net_cents is None:
            missing.append(f"Netto ({settings.default_currency})")
        missing.extend(services.cost_allocation_service.validate_for_receipt(receipt))
        return list(dict.fromkeys(missing))

    def open_in_new_tab(url: str) -> None:
        ui.run_javascript(f"window.open({json.dumps(url)}, '_blank')")

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
                    ui.notify(f"Import fehlgeschlagen: {exc}", type="negative")

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
                        with ui.card().classes("bm-card p-4"):
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

                with ui.row().classes("w-full items-center justify-between"):
                    with ui.row().classes("gap-2"):
                        active_view_button = ui.button("Belege").props("unelevated no-caps").classes(
                            "bm-view-mode-btn bm-segment-btn"
                        )
                        deleted_view_button = ui.button("Gelöschte Belege").props("unelevated no-caps").classes(
                            "bm-view-mode-btn bm-segment-btn"
                        )
                    with ui.row().classes("gap-2"):
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
                                    ui.notify(f"Endgültiges Löschen fehlgeschlagen: {exc}", type="negative")
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
                        ui.notify(f"Löschen fehlgeschlagen: {exc}", type="negative")
                        return
                    render_results()

                def restore_receipt(receipt_id: int) -> None:
                    if receipt_id <= 0:
                        return
                    try:
                        services.receipt_service.restore_from_trash(receipt_id)
                        ui.notify("Beleg wiederhergestellt", type="positive")
                    except Exception as exc:
                        ui.notify(f"Wiederherstellung fehlgeschlagen: {exc}", type="negative")
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
                        table = _erp_table(columns=columns, rows=rows, row_key="id", rows_per_page=25)

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
                            """
                            <q-td :props="props" class="text-right">
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

        with _shell("/belege", "Belegdetail"):
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
                projects = list(session.exec(select(Project).order_by(Project.name)).all())
                suppliers = list(session.exec(select(Supplier).order_by(Supplier.name)).all())

                selected_cost_type_ids: list[int] = []
                selected_subcategory_ids: list[int] = []
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
            missing_fields = missing_required_fields(receipt)

            with ui.card().classes("bm-card bm-detail-card p-4 w-full"):
                with ui.row().classes("bm-detail-toolbar w-full items-center justify-between"):
                    with ui.row().classes("items-center gap-2"):
                        cancel_btn = ui.button(
                            icon="close",
                            on_click=lambda: ui.navigate.to("/belege"),
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
                                on_click=lambda: _detail_save(),
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
                        completeness = "Vollständig" if not missing_fields else "Pflichtangaben fehlen"
                        ui.label(f"Vollständigkeit: {completeness}").classes(
                            "text-sm text-green-700" if not missing_fields else "text-sm text-amber-700"
                        )
                        if missing_fields:
                            ui.label(f"Fehlt: {', '.join(missing_fields)}").classes("text-xs text-amber-700")
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
                            ).props("flat round dense").classes("bm-inline-create-btn")
                            supplier_add_btn.tooltip("Neuen Anbieter anlegen")
                        document_type_value = (
                            receipt.document_type
                            if receipt.document_type in {DOC_TYPE_INVOICE, DOC_TYPE_CREDIT_NOTE}
                            else DOC_TYPE_INVOICE
                        )
                        with ui.row().classes("w-full items-center"):
                            document_type_input = ui.toggle(
                                {DOC_TYPE_INVOICE: "Rechnung", DOC_TYPE_CREDIT_NOTE: "Gutschrift"},
                                value=document_type_value,
                            ).props("unelevated no-caps").classes("bm-view-mode-btn bm-doc-type-toggle")

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

                        with ui.row().classes("w-full gap-2 items-end wrap"):
                            gross_input = ui.input(
                                f"Brutto ({settings.default_currency})",
                                value=gross_default,
                            ).props("input-debounce=0").classes("min-w-0 flex-1")
                            vat_input = ui.input("USt-Satz (%)", value=vat_default).props("input-debounce=0").classes(
                                "w-36"
                            )
                            net_input = ui.input(
                                f"Netto ({settings.default_currency})",
                                value=_format_cents(receipt.amount_net_cents, settings.default_currency),
                            ).props("readonly").classes("min-w-0 flex-1")

                        cost_type_select_options = {item.id: item.name for item in cost_types if item.id is not None}
                        project_map = {item.id: item.name for item in projects if item.id is not None}
                        subcategories_by_type: dict[int, list[CostSubcategory]] = {}
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
                                return value
                            if isinstance(value, str):
                                text = value.strip()
                                if not text:
                                    return None
                                if text.isdigit():
                                    try:
                                        return int(text)
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
                            nonlocal projects, project_map
                            with Session(engine) as session:
                                projects = list(session.exec(select(Project).order_by(Project.name)).all())
                            project_map = {item.id: item.name for item in projects if item.id is not None}

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
                                        with Session(engine) as session:
                                            existing = session.exec(
                                                select(Supplier).where(func.lower(Supplier.name) == name.casefold())
                                            ).first()
                                            if existing:
                                                existing.active = bool(active_input.value)
                                                existing.updated_at = datetime.now(timezone.utc)
                                                session.add(existing)
                                                session.commit()
                                                supplier_id = existing.id
                                                ui.notify("Anbieter existierte bereits und wurde aktualisiert", type="positive")
                                            else:
                                                supplier = Supplier(name=name, active=bool(active_input.value))
                                                session.add(supplier)
                                                session.commit()
                                                supplier_id = supplier.id
                                                ui.notify("Anbieter angelegt", type="positive")
                                        reload_supplier_options(selected_id=supplier_id if isinstance(supplier_id, int) else None)
                                        dialog.close()
                                    except Exception as exc:
                                        ui.notify(f"Anbieter konnte nicht angelegt werden: {exc}", type="negative")

                                with ui.row().classes("w-full justify-end gap-2"):
                                    ui.button("Abbrechen", on_click=dialog.close).props("flat")
                                    ui.button("Anlegen", on_click=save_supplier).props("color=primary")
                            dialog.open()

                        def open_quick_project_dialog(row_target: dict[str, Any] | None = None) -> None:
                            with ui.dialog() as dialog, ui.card().classes("p-4 w-[520px] max-w-full"):
                                ui.label("Neues Projekt").classes("text-lg font-semibold")
                                name_input = ui.input("Projektname").classes("w-full")
                                created_on_input = ui.input("Erschaffen am (optional)").props("type=date clearable").classes("w-full")
                                active_input = ui.checkbox("Aktiv", value=True)

                                def save_project() -> None:
                                    name = (name_input.value or "").strip()
                                    if not name:
                                        ui.notify("Projektname fehlt", type="negative")
                                        return
                                    try:
                                        with Session(engine) as session:
                                            existing = session.exec(
                                                select(Project).where(func.lower(Project.name) == name.casefold())
                                            ).first()
                                            if existing:
                                                existing.active = bool(active_input.value)
                                                existing.created_on = _parse_iso_date(created_on_input.value)
                                                session.add(existing)
                                                session.commit()
                                                project_id = existing.id
                                                ui.notify("Projekt existierte bereits und wurde aktualisiert", type="positive")
                                            else:
                                                project = Project(
                                                    name=name,
                                                    color=DEFAULT_PROJECT_COLOR,
                                                    active=bool(active_input.value),
                                                    created_on=_parse_iso_date(created_on_input.value),
                                                )
                                                session.add(project)
                                                session.commit()
                                                project_id = project.id
                                                ui.notify("Projekt angelegt", type="positive")
                                        reload_project_options(selected_id=project_id if isinstance(project_id, int) else None)
                                        if row_target is not None and isinstance(project_id, int):
                                            row_target["project_id"] = project_id
                                        render_allocation_editor()
                                        refresh_allocation_summary()
                                        dialog.close()
                                    except Exception as exc:
                                        ui.notify(f"Projekt konnte nicht angelegt werden: {exc}", type="negative")

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

                        def refresh_net_preview() -> None:
                            try:
                                gross_cents = _parse_money_to_cents(gross_input.value, allow_negative=True)
                                if gross_cents is None:
                                    net_input.value = "-"
                                    return
                                vat_raw = (vat_input.value or "").strip()
                                vat_rate = float(vat_raw.replace(",", ".")) if vat_raw else settings.default_vat_rate_percent
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

                        gross_input.on("update:model-value", lambda _: schedule_net_preview())
                        vat_input.on("update:model-value", lambda _: schedule_net_preview())
                        gross_input.on("keydown.enter", lambda _: refresh_net_preview())
                        vat_input.on("keydown.enter", lambda _: refresh_net_preview())
                        gross_input.on("blur", lambda _: normalize_gross_on_blur())
                        document_type_input.on("update:model-value", lambda _: on_document_type_change())
                        refresh_net_preview()

                        def refresh_allocation_summary() -> None:
                            try:
                                gross_cents = _parse_money_to_cents(gross_input.value, allow_negative=True)
                            except ValueError:
                                gross_cents = None
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
                                [row.get("amount_text") for row in allocation_rows],
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
                                            ).props("flat round dense").classes("bm-inline-create-btn")
                                            project_add_btn.tooltip("Neues Projekt anlegen")

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
                                        lambda _: (update_standard_fields(), refresh_allocation_summary())
                                    )
                                    subcategory_input.on_value_change(
                                        lambda _: (update_subcategory(), refresh_allocation_summary())
                                    )
                                    project_input.on_value_change(
                                        lambda _: (update_subcategory(), refresh_allocation_summary())
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
                                                    ).props("flat round dense").classes("bm-inline-create-btn")
                                                    project_add_btn.tooltip("Neues Projekt anlegen")
                                                if len(allocation_rows) > 1:
                                                    ui.button(
                                                        icon="remove_circle",
                                                        on_click=lambda i=idx: remove_allocation_row(i),
                                                    ).props("flat round dense color=negative")

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
                                                lambda _, fn=update_split_cost_type: (fn(), refresh_allocation_summary())
                                            )
                                            subcategory_input.on_value_change(
                                                lambda _, fn=update_split_subcategory_project: (
                                                    fn(),
                                                    refresh_allocation_summary(),
                                                )
                                            )
                                            project_input.on_value_change(
                                                lambda _, fn=update_split_subcategory_project: (
                                                    fn(),
                                                    refresh_allocation_summary(),
                                                )
                                            )
                                            amount_input.on(
                                                "update:model-value",
                                                lambda e, fn=update_split_amount, inp=amount_input: (
                                                    fn(_extract_model_value(e, inp.value)),
                                                    refresh_allocation_summary(),
                                                ),
                                            )
                                            amount_input.on(
                                                "keydown.enter",
                                                lambda _, fn=update_split_amount, inp=amount_input: (
                                                    fn(inp.value),
                                                    refresh_allocation_summary(),
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

                        def remove_allocation_row(index: int) -> None:
                            nonlocal allocation_rows
                            if len(allocation_rows) <= 1:
                                return
                            allocation_rows = [row for idx, row in enumerate(allocation_rows) if idx != index]
                            render_allocation_editor()
                            refresh_allocation_summary()

                        apply_document_type_sign_to_inputs(flip_existing=False)
                        render_allocation_editor()
                        refresh_allocation_summary()

                        if is_deleted:
                            date_input.disable()
                            supplier_input.disable()
                            supplier_add_btn.disable()
                            document_type_input.disable()
                            gross_input.disable()
                            vat_input.disable()

                        def _detail_move_to_deleted(receipt_id_for_action: int | None) -> None:
                            if not receipt_id_for_action:
                                return
                            try:
                                services.receipt_service.move_to_trash(receipt_id_for_action)
                                ui.notify("Beleg in Gelöschte Belege verschoben", type="positive")
                                ui.navigate.to("/belege")
                            except Exception as exc:
                                ui.notify(f"Löschen fehlgeschlagen: {exc}", type="negative")

                        def _detail_restore(receipt_id_for_action: int | None) -> None:
                            if not receipt_id_for_action:
                                return
                            try:
                                services.receipt_service.restore_from_trash(receipt_id_for_action)
                                ui.notify("Beleg wiederhergestellt", type="positive")
                                ui.navigate.to("/belege")
                            except Exception as exc:
                                ui.notify(f"Wiederherstellung fehlgeschlagen: {exc}", type="negative")

                        def _detail_save() -> None:
                            try:
                                document_type = current_document_type()
                                amount_gross_cents = _parse_money_to_cents(gross_input.value, allow_negative=True)
                                vat_raw = (vat_input.value or "").strip()
                                vat_rate_percent = (
                                    float(vat_raw.replace(",", ".")) if vat_raw else settings.default_vat_rate_percent
                                )
                                if vat_rate_percent < 0:
                                    raise ValueError("USt-Satz darf nicht negativ sein")
                                if amount_gross_cents is not None:
                                    if document_type == DOC_TYPE_INVOICE and amount_gross_cents < 0:
                                        raise ValueError("Bei Rechnung muss der Bruttobetrag >= 0 sein")
                                    if document_type == DOC_TYPE_CREDIT_NOTE and amount_gross_cents > 0:
                                        raise ValueError("Bei Gutschrift muss der Bruttobetrag <= 0 sein")
                                if amount_gross_cents is None:
                                    vat_rate_percent = None

                                allocation_payload: list[AllocationInput] = []
                                if not split_enabled:
                                    ensure_standard_single_row()
                                    row = allocation_rows[0]
                                    allocation_payload.append(
                                        AllocationInput(
                                            cost_type_id=int(row.get("cost_type_id") or 0),
                                            cost_subcategory_id=int(row.get("cost_subcategory_id") or 0),
                                            project_id=int(row["project_id"]) if row.get("project_id") else None,
                                            cost_area_id=None,
                                            amount_cents=amount_gross_cents or 0,
                                            position=1,
                                        )
                                    )
                                else:
                                    for idx, row in enumerate(allocation_rows):
                                        amount_cents = _parse_money_to_cents(row.get("amount_text"), allow_negative=True)
                                        allocation_payload.append(
                                            AllocationInput(
                                                cost_type_id=int(row.get("cost_type_id") or 0),
                                                cost_subcategory_id=int(row.get("cost_subcategory_id") or 0),
                                                project_id=int(row["project_id"]) if row.get("project_id") else None,
                                                cost_area_id=None,
                                                amount_cents=amount_cents or 0,
                                                position=idx + 1,
                                            )
                                        )

                                services.receipt_service.update_metadata(
                                    receipt_id=rid,
                                    doc_date=_parse_iso_date(date_input.value),
                                    supplier_id=int(supplier_input.value) if supplier_input.value else None,
                                    amount_gross_cents=amount_gross_cents,
                                    vat_rate_percent=vat_rate_percent,
                                    document_type=document_type,
                                )
                                services.cost_allocation_service.save_allocations(
                                    receipt_id=rid,
                                    allocations=allocation_payload,
                                )
                            except Exception as exc:
                                ui.notify(f"Speichern fehlgeschlagen: {exc}", type="negative")
                                return

                            ui.notify("Beleg gespeichert", type="positive")
                            ui.navigate.to("/belege")

    @ui.page("/projekte")
    def projects_page() -> None:
        with _shell("/projekte", "Projekte"):
            with ui.card().classes("bm-card p-4 w-full"):
                def open_create_project_dialog() -> None:
                    with ui.dialog() as dialog, ui.card().classes("p-4 w-[560px] max-w-full"):
                        ui.label("Neues Projekt").classes("text-lg font-semibold")
                        name_input = ui.input("Projektname").classes("w-full")
                        created_on_input = ui.input("Erschaffen am (optional)").props("type=date clearable").classes("w-full")
                        active_input = ui.checkbox("Aktiv", value=True)

                        def add_project() -> None:
                            name = (name_input.value or "").strip()
                            if not name:
                                ui.notify("Projektname fehlt", type="negative")
                                return
                            try:
                                with Session(engine) as session:
                                    existing = session.exec(
                                        select(Project).where(func.lower(Project.name) == name.casefold())
                                    ).first()
                                    if existing:
                                        existing.active = bool(active_input.value)
                                        existing.created_on = _parse_iso_date(created_on_input.value)
                                        session.add(existing)
                                        session.commit()
                                        ui.notify("Projekt existierte bereits und wurde aktualisiert", type="positive")
                                    else:
                                        project = Project(
                                            name=name,
                                            color=DEFAULT_PROJECT_COLOR,
                                            active=bool(active_input.value),
                                            created_on=_parse_iso_date(created_on_input.value),
                                        )
                                        session.add(project)
                                        session.commit()
                                        ui.notify("Projekt angelegt", type="positive")
                                dialog.close()
                                render_projects()
                            except Exception as exc:
                                ui.notify(f"Projekt konnte nicht angelegt werden: {exc}", type="negative")

                        with ui.row().classes("w-full justify-end gap-2"):
                            ui.button("Abbrechen", on_click=dialog.close).props("flat")
                            ui.button("Anlegen", on_click=add_project).props("color=primary")
                    dialog.open()

                with ui.row().classes("w-full justify-end"):
                    ui.button("Projekt anlegen", icon="add", on_click=open_create_project_dialog).props("color=primary")

                project_column = ui.column().classes("w-full gap-2")

                def delete_project(project_id: int) -> None:
                    old_cover_path: str | None = None
                    with Session(engine) as session:
                        session.exec(delete(CostAllocation).where(CostAllocation.project_id == project_id))
                        project = session.get(Project, project_id)
                        if project:
                            old_cover_path = project.cover_image_path
                            session.delete(project)
                        session.commit()
                    safe_delete_file(old_cover_path)
                    ui.notify("Projekt entfernt", type="positive")
                    render_projects()

                def open_cover_dialog(project_id: int) -> None:
                    with ui.dialog() as dialog, ui.card().classes("p-4 w-[520px] max-w-full"):
                        ui.label("Projekt-Cover setzen").classes("text-lg font-semibold")
                        ui.label("Beim Upload wird das Bild als optimiertes WebP gespeichert.").classes("text-sm text-slate-600")

                        async def handle_cover_upload(event: events.MultiUploadEventArguments) -> None:
                            if not event.files:
                                return
                            file_upload = event.files[0]
                            old_cover_path: str | None = None
                            try:
                                with Session(engine) as session:
                                    project = session.get(Project, project_id)
                                    if not project:
                                        raise ValueError("Projekt nicht gefunden")
                                    old_cover_path = project.cover_image_path
                                new_cover_path = await save_uploaded_work_cover(file_upload, project_id)
                                with Session(engine) as session:
                                    project = session.get(Project, project_id)
                                    if not project:
                                        raise ValueError("Projekt nicht gefunden")
                                    project.cover_image_path = str(new_cover_path)
                                    session.add(project)
                                    session.commit()
                                if old_cover_path and old_cover_path != str(new_cover_path):
                                    safe_delete_file(old_cover_path)
                                ui.notify("Projekt-Cover gespeichert", type="positive")
                                dialog.close()
                                render_projects()
                            except Exception as exc:
                                ui.notify(f"Cover konnte nicht gespeichert werden: {exc}", type="negative")

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
                                "created_on": project.created_on.isoformat() if project.created_on else "-",
                                "status": "aktiv" if project.active else "inaktiv",
                            }
                            for project in projects
                            if project.id is not None
                        ]
                        columns = [
                            {"name": "cover", "label": "Cover", "field": "cover", "align": "left"},
                            {"name": "name", "label": "Projekt", "field": "name", "align": "left", "sortable": True},
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
                        table = _erp_table(columns=columns, rows=rows, row_key="id", rows_per_page=20)
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
                            """
                            <q-td :props="props" class="text-right">
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

        with _shell("/projekte", "Projektdetail"):
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
                    ui.button("Zurück zu Projekten", icon="arrow_back", on_click=lambda: ui.navigate.to("/projekte")).props(
                        "flat"
                    )

                with ui.element("div").classes("bm-detail-grid w-full"):
                    with ui.column().classes("bm-detail-preview gap-3"):
                        if cover_url:
                            ui.image(cover_url).classes("w-full max-h-[72vh] object-contain rounded-xl bg-white")
                        else:
                            with ui.element("div").classes(
                                "w-full h-[320px] rounded-xl bg-slate-100 flex items-center justify-center"
                            ):
                                ui.icon("image", size="56px")
                        ui.label("Cover-Bilder werden als optimiertes WebP gespeichert.").classes("text-xs text-slate-600")

                        async def handle_cover_upload(event: events.MultiUploadEventArguments) -> None:
                            if not event.files:
                                return
                            file_upload = event.files[0]
                            old_cover_path: str | None = None
                            try:
                                with Session(engine) as session:
                                    project_for_cover = session.get(Project, pid)
                                    if not project_for_cover:
                                        raise ValueError("Projekt nicht gefunden")
                                    old_cover_path = project_for_cover.cover_image_path
                                new_cover_path = await save_uploaded_work_cover(file_upload, pid)
                                with Session(engine) as session:
                                    project_for_cover = session.get(Project, pid)
                                    if not project_for_cover:
                                        raise ValueError("Projekt nicht gefunden")
                                    project_for_cover.cover_image_path = str(new_cover_path)
                                    session.add(project_for_cover)
                                    session.commit()
                                if old_cover_path and old_cover_path != str(new_cover_path):
                                    safe_delete_file(old_cover_path)
                                ui.notify("Projekt-Cover gespeichert", type="positive")
                                ui.navigate.to(f"/projekte/{pid}")
                            except Exception as exc:
                                ui.notify(f"Cover konnte nicht gespeichert werden: {exc}", type="negative")

                        upload = ui.upload(
                            multiple=False,
                            auto_upload=True,
                            on_multi_upload=handle_cover_upload,
                            label="Cover ersetzen",
                        ).classes("w-full")
                        upload.props("accept=.jpg,.jpeg,.png,.heic,.heif")

                    with ui.column().classes("bm-detail-form gap-3"):
                        name_input = ui.input("Projektname", value=project.name).classes("w-full")
                        created_on_input = ui.input(
                            "Erschaffen am (optional)",
                            value=project.created_on.isoformat() if project.created_on else "",
                        ).props("type=date clearable").classes("w-full")
                        active_input = ui.checkbox("Aktiv", value=project.active)

                        with ui.row().classes("w-full justify-between gap-2"):
                            ui.button(
                                "Projekt löschen",
                                icon="delete",
                                on_click=lambda: _delete_and_back(),
                            ).props("flat color=negative")
                            ui.button("Speichern", on_click=lambda: _save_project()).props("color=primary")

                        def _save_project() -> None:
                            name = (name_input.value or "").strip()
                            if not name:
                                ui.notify("Projektname fehlt", type="negative")
                                return
                            try:
                                with Session(engine) as session:
                                    current = session.get(Project, pid)
                                    if not current:
                                        raise ValueError("Projekt nicht gefunden")
                                    duplicate = session.exec(
                                        select(Project).where(
                                            func.lower(Project.name) == name.casefold(),
                                            Project.id != pid,
                                        )
                                    ).first()
                                    if duplicate:
                                        raise ValueError("Projektname existiert bereits")

                                    current.name = name
                                    current.active = bool(active_input.value)
                                    current.created_on = _parse_iso_date(created_on_input.value)
                                    session.add(current)
                                    session.commit()
                                ui.notify("Projekt gespeichert", type="positive")
                                ui.navigate.to(f"/projekte/{pid}")
                            except Exception as exc:
                                ui.notify(f"Speichern fehlgeschlagen: {exc}", type="negative")

                        def _delete_and_back() -> None:
                            old_cover_path: str | None = None
                            with Session(engine) as session:
                                session.exec(delete(CostAllocation).where(CostAllocation.project_id == pid))
                                current = session.get(Project, pid)
                                if current:
                                    old_cover_path = current.cover_image_path
                                    session.delete(current)
                                session.commit()
                            safe_delete_file(old_cover_path)
                            ui.notify("Projekt entfernt", type="positive")
                            ui.navigate.to("/projekte")

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
                                with Session(engine) as session:
                                    existing = session.exec(
                                        select(Supplier).where(func.lower(Supplier.name) == name.casefold())
                                    ).first()
                                    if existing:
                                        existing.active = bool(active_input.value)
                                        existing.updated_at = datetime.now(timezone.utc)
                                        session.add(existing)
                                        session.commit()
                                        ui.notify("Anbieter existierte bereits und wurde aktualisiert", type="positive")
                                    else:
                                        supplier = Supplier(name=name, active=bool(active_input.value))
                                        session.add(supplier)
                                        session.commit()
                                        ui.notify("Anbieter angelegt", type="positive")
                                dialog.close()
                                render_suppliers()
                            except Exception as exc:
                                ui.notify(f"Anbieter konnte nicht angelegt werden: {exc}", type="negative")

                        with ui.row().classes("w-full justify-end gap-2"):
                            ui.button("Abbrechen", on_click=dialog.close).props("flat")
                            ui.button("Anlegen", on_click=add_supplier).props("color=primary")
                    dialog.open()

                with ui.row().classes("w-full justify-end"):
                    ui.button("Anbieter anlegen", icon="add", on_click=open_create_supplier_dialog).props("color=primary")

                suppliers_column = ui.column().classes("w-full gap-2")

                def delete_supplier(supplier_id: int) -> None:
                    with Session(engine) as session:
                        session.exec(sa_update(Receipt).where(Receipt.supplier_id == supplier_id).values(supplier_id=None))
                        supplier = session.get(Supplier, supplier_id)
                        if supplier:
                            session.delete(supplier)
                        session.commit()
                    ui.notify("Anbieter entfernt", type="positive")
                    render_suppliers()

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
                                with Session(engine) as session:
                                    supplier = session.get(Supplier, supplier_id)
                                    if not supplier:
                                        raise ValueError("Anbieter nicht gefunden")

                                    duplicate = session.exec(
                                        select(Supplier).where(
                                            func.lower(Supplier.name) == name.casefold(),
                                            Supplier.id != supplier_id,
                                        )
                                    ).first()
                                    if duplicate:
                                        raise ValueError("Anbietername existiert bereits")

                                    supplier.name = name
                                    supplier.active = bool(active_edit.value)
                                    supplier.updated_at = datetime.now(timezone.utc)
                                    session.add(supplier)
                                    session.commit()
                                ui.notify("Anbieter gespeichert", type="positive")
                                dialog.close()
                                render_suppliers()
                            except Exception as exc:
                                ui.notify(f"Speichern fehlgeschlagen: {exc}", type="negative")

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
                            }
                            for supplier in suppliers
                            if supplier.id is not None
                        ]
                        columns = [
                            {"name": "name", "label": "Anbieter", "field": "name", "align": "left", "sortable": True},
                            {"name": "status", "label": "Status", "field": "status", "align": "left"},
                            {"name": "actions", "label": "Aktionen", "field": "actions", "align": "right"},
                        ]
                        table = _erp_table(columns=columns, rows=rows, row_key="id", rows_per_page=20)
                        table.add_slot(
                            "body-cell-actions",
                            """
                            <q-td :props="props" class="text-right">
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
                def ensure_default_subcategory(session: Session, category_id: int) -> None:
                    category = session.get(CostType, category_id)
                    if not category:
                        return
                    expected_name = default_subcategory_name_for_cost_type(category.name)
                    existing_items = list(
                        session.exec(select(CostSubcategory).where(CostSubcategory.cost_type_id == category_id)).all()
                    )
                    expected_item = next(
                        (item for item in existing_items if item.name.casefold() == expected_name.casefold()),
                        None,
                    )
                    system_defaults = [item for item in existing_items if item.is_system_default]
                    legacy_default = next(
                        (item for item in existing_items if item.name.casefold() == DEFAULT_SUBCATEGORY_NAME.casefold()),
                        None,
                    )
                    default_item = expected_item or (system_defaults[0] if system_defaults else legacy_default)
                    if default_item:
                        if default_item.name != expected_name:
                            default_item.name = expected_name
                        if not default_item.is_system_default:
                            default_item.is_system_default = True
                        if not default_item.active:
                            default_item.active = True
                        if default_item.archived_with_parent:
                            default_item.archived_with_parent = False
                        session.add(default_item)
                    else:
                        default_item = CostSubcategory(
                            cost_type_id=category_id,
                            name=expected_name,
                            is_system_default=True,
                            active=True,
                            archived_with_parent=False,
                        )
                        session.add(default_item)
                        session.flush()

                    for item in system_defaults:
                        if default_item.id is not None and item.id == default_item.id:
                            continue
                        if item.is_system_default:
                            item.is_system_default = False
                            session.add(item)

                def is_cost_type_used(session: Session, category_id: int) -> bool:
                    used = session.exec(select(CostAllocation.id).where(CostAllocation.cost_type_id == category_id)).first()
                    return used is not None

                def is_subcategory_used(session: Session, subcategory_id: int) -> bool:
                    used = session.exec(
                        select(CostAllocation.id).where(CostAllocation.cost_subcategory_id == subcategory_id)
                    ).first()
                    return used is not None

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
                                with Session(engine) as session:
                                    existing = session.exec(
                                        select(CostType).where(func.lower(CostType.name) == name.casefold())
                                    ).first()
                                    if existing:
                                        existing.icon = icon
                                        if not existing.active:
                                            existing.active = True
                                        session.add(existing)
                                        if existing.id is not None:
                                            for item in session.exec(
                                                select(CostSubcategory).where(
                                                    CostSubcategory.cost_type_id == existing.id,
                                                    CostSubcategory.archived_with_parent.is_(True),
                                                )
                                            ).all():
                                                item.active = True
                                                item.archived_with_parent = False
                                                session.add(item)
                                            ensure_default_subcategory(session, existing.id)
                                        session.commit()
                                        ui.notify("Kostenkategorie existierte bereits und wurde aktualisiert", type="positive")
                                    else:
                                        category = CostType(name=name, icon=icon, active=True)
                                        session.add(category)
                                        session.flush()
                                        if category.id is not None:
                                            ensure_default_subcategory(session, category.id)
                                        session.commit()
                                        ui.notify("Kostenkategorie angelegt", type="positive")
                                dialog.close()
                                set_category_view("active")
                            except Exception as exc:
                                ui.notify(f"Kostenkategorie konnte nicht angelegt werden: {exc}", type="negative")

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
                        with Session(engine) as session:
                            category = session.get(CostType, category_id)
                            if not category:
                                raise ValueError("Kostenkategorie nicht gefunden")

                            if category_view_mode == "archived":
                                category.active = True
                                session.add(category)
                                subcategories = list(
                                    session.exec(
                                        select(CostSubcategory).where(CostSubcategory.cost_type_id == category_id)
                                    ).all()
                                )
                                for item in subcategories:
                                    if item.archived_with_parent:
                                        item.active = True
                                        item.archived_with_parent = False
                                        session.add(item)
                                ensure_default_subcategory(session, category_id)
                                session.commit()
                                ui.notify("Kostenkategorie wiederhergestellt", type="positive")
                                render_categories()
                                return

                            if is_cost_type_used(session, category_id):
                                category.active = False
                                session.add(category)
                                subcategories = list(
                                    session.exec(
                                        select(CostSubcategory).where(CostSubcategory.cost_type_id == category_id)
                                    ).all()
                                )
                                for item in subcategories:
                                    if item.active:
                                        item.active = False
                                        item.archived_with_parent = True
                                        session.add(item)
                                session.commit()
                                ui.notify("Kostenkategorie archiviert", type="positive")
                                render_categories()
                                return

                            session.exec(delete(CostSubcategory).where(CostSubcategory.cost_type_id == category_id))
                            session.delete(category)
                            session.commit()
                            ui.notify("Unbenutzte Kostenkategorie gelöscht", type="positive")
                    except Exception as exc:
                        ui.notify(str(exc), type="negative")
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
                        new_subcategory_input = ui.input("Neue Unterkategorie").classes("w-full")
                        show_archived_subcategories = False
                        subcategory_column = ui.column().classes("w-full gap-2")
                        archived_toggle_row = ui.row().classes("w-full")
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

                            subcategory_column.clear()
                            with subcategory_column:
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

                                if show_archived_subcategories:
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
                                with Session(engine) as session:
                                    category = session.get(CostType, category_id)
                                    if not category:
                                        raise ValueError("Kostenkategorie nicht gefunden")
                                    if not category.active:
                                        raise ValueError("Unterkategorien können nur bei aktiven Kostenkategorien ergänzt werden")

                                    duplicate = session.exec(
                                        select(CostSubcategory).where(
                                            CostSubcategory.cost_type_id == category_id,
                                            func.lower(CostSubcategory.name) == name.casefold(),
                                        )
                                    ).first()
                                    if duplicate:
                                        if duplicate.active:
                                            raise ValueError("Unterkategorie existiert bereits")
                                        duplicate.active = True
                                        duplicate.archived_with_parent = False
                                        session.add(duplicate)
                                        session.commit()
                                        ui.notify("Unterkategorie wiederhergestellt", type="positive")
                                        new_subcategory_input.value = ""
                                        show_archived_subcategories = False
                                        render_archived_toggle()
                                        render_subcategories()
                                        return

                                    session.add(
                                        CostSubcategory(
                                            cost_type_id=category_id,
                                            name=name,
                                            is_system_default=False,
                                            active=True,
                                            archived_with_parent=False,
                                        )
                                    )
                                    session.commit()
                                new_subcategory_input.value = ""
                                ui.notify("Unterkategorie angelegt", type="positive")
                                show_archived_subcategories = False
                                render_archived_toggle()
                                render_subcategories()
                            except Exception as exc:
                                ui.notify(f"Unterkategorie konnte nicht angelegt werden: {exc}", type="negative")

                        def run_subcategory_primary_action(subcategory_id: int | None) -> None:
                            if not subcategory_id:
                                return
                            try:
                                with Session(engine) as session:
                                    subcategory = session.get(CostSubcategory, subcategory_id)
                                    if not subcategory:
                                        raise ValueError("Unterkategorie nicht gefunden")
                                    if subcategory.is_system_default:
                                        raise ValueError("Die Standard-Unterkategorie kann nicht gelöscht oder archiviert werden")

                                    if is_subcategory_used(session, subcategory_id):
                                        subcategory.active = False
                                        subcategory.archived_with_parent = False
                                        session.add(subcategory)
                                        session.commit()
                                        ui.notify("Unterkategorie archiviert", type="positive")
                                        render_subcategories()
                                        return

                                    session.delete(subcategory)
                                    session.commit()
                                ui.notify("Unterkategorie gelöscht", type="positive")
                                render_subcategories()
                            except Exception as exc:
                                ui.notify(str(exc), type="negative")

                        def restore_subcategory(subcategory_id: int | None) -> None:
                            if not subcategory_id:
                                return
                            try:
                                with Session(engine) as session:
                                    subcategory = session.get(CostSubcategory, subcategory_id)
                                    if not subcategory:
                                        raise ValueError("Unterkategorie nicht gefunden")
                                    category = session.get(CostType, subcategory.cost_type_id)
                                    if not category or not category.active:
                                        raise ValueError(
                                            "Unterkategorie kann nur wiederhergestellt werden, wenn die Kostenkategorie aktiv ist"
                                        )
                                    subcategory.active = True
                                    subcategory.archived_with_parent = False
                                    session.add(subcategory)
                                    session.commit()
                                ui.notify("Unterkategorie wiederhergestellt", type="positive")
                                render_subcategories()
                            except Exception as exc:
                                ui.notify(str(exc), type="negative")

                        def save_category() -> None:
                            name = (name_edit.value or "").strip()
                            if not name:
                                ui.notify("Name fehlt", type="negative")
                                return
                            try:
                                with Session(engine) as session:
                                    category = session.get(CostType, category_id)
                                    if not category:
                                        raise ValueError("Kostenkategorie nicht gefunden")
                                    duplicate = session.exec(
                                        select(CostType).where(
                                            func.lower(CostType.name) == name.casefold(),
                                            CostType.id != category_id,
                                        )
                                    ).first()
                                    if duplicate:
                                        raise ValueError("Name existiert bereits")
                                    category.name = name
                                    category.icon = str(icon_edit.value or DEFAULT_COST_TYPE_ICON)
                                    session.add(category)
                                    ensure_default_subcategory(session, category_id)
                                    session.commit()
                                ui.notify("Kostenkategorie gespeichert", type="positive")
                                dialog.close()
                                render_categories()
                            except Exception as exc:
                                ui.notify(f"Speichern fehlgeschlagen: {exc}", type="negative")

                        with ui.row().classes("w-full gap-2 items-end wrap"):
                            ui.button("Unterkategorie hinzufügen", icon="add", on_click=add_subcategory).props("flat")
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
                        table = _erp_table(columns=columns, rows=rows, row_key="id", rows_per_page=20)
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
                            """
                            <q-td :props="props" class="text-right">
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
                summary_container = ui.column().classes("w-full gap-2")
                categories_container = ui.column().classes("w-full gap-2")
                subcategories_container = ui.column().classes("w-full gap-2")

                def amount_state(total_cents: int) -> tuple[str, str]:
                    if total_cents > 0:
                        return "Ausgabe", "bm-amount-expense"
                    if total_cents < 0:
                        return "Einnahme", "bm-amount-income"
                    return "Neutral", "bm-amount-neutral"

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
                        table = _erp_table(columns=columns, rows=rows, row_key="id", rows_per_page=20)
                        table.add_slot(
                            "body-cell-total",
                            """
                            <q-td :props="props" class="text-right">
                              <span :class="props.row.total_class">{{ props.row.total }}</span>
                            </q-td>
                            """,
                        )

                def render_report() -> None:
                    nonlocal selected_cost_type_id, selected_cost_type_name
                    summary_container.clear()
                    categories_container.clear()

                    from_value = _parse_iso_date(str(date_from.value or ""))
                    to_value = _parse_iso_date(str(date_to.value or ""))

                    with summary_container:
                        if not from_value or not to_value:
                            with ui.card().classes("bm-card p-3"):
                                ui.label("Bitte Startdatum und Enddatum eingeben.")
                            subcategories_container.clear()
                            return
                        if from_value > to_value:
                            with ui.card().classes("bm-card p-3"):
                                ui.label("Startdatum darf nicht nach dem Enddatum liegen.")
                            subcategories_container.clear()
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
                            return

                        category_name_by_id = {item.cost_type_id: item.cost_type_name for item in report.totals_by_cost_type}
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
                                }
                            )
                        columns = [
                            {"name": "name", "label": "Kostenkategorie", "field": "name", "align": "left", "sortable": True},
                            {
                                "name": "total",
                                "label": f"Summe ({settings.default_currency})",
                                "field": "total",
                                "align": "right",
                                "sortable": True,
                            },
                        ]
                        table = _erp_table(columns=columns, rows=rows, row_key="id", rows_per_page=20)
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
        from .. import __version__
        from ..config import settings
        from ..legal import APP_COPYRIGHT, APP_LICENSE_ID, ThirdPartyNotice, get_third_party_notices

        with _shell("/einstellungen", "Einstellungen"):
            with ui.card().classes("bm-card p-4 w-full"):
                ui.label("Version & Rechtliches").classes("text-lg font-semibold")
                ui.label(f"App-Version: {__version__}")
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
                                }
                                for idx, item in enumerate(notices, start=1)
                            ]
                            columns = [
                                {"name": "name", "label": "Paket", "field": "name", "align": "left", "sortable": True},
                                {"name": "version", "label": "Version", "field": "version", "align": "left", "sortable": True},
                                {"name": "license", "label": "Lizenz", "field": "license", "align": "left", "sortable": True},
                                {"name": "source_label", "label": "Quelle", "field": "source_label", "align": "left"},
                            ]
                            table = _erp_table(columns=columns, rows=rows, row_key="id", rows_per_page=15).classes(
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
                ui.label(f"Projekt-Cover: {settings.works_cover_dir}")
                ui.label(f"OCR-Sprachen: {settings.ocr_languages}")
                ui.label(f"Währung (Default): {settings.default_currency}")
                ui.label(f"USt-Satz (Default): {settings.default_vat_rate_percent:.2f}%")
                ui.label("Single-User v1 ohne Login.").classes("text-sm text-slate-600")
