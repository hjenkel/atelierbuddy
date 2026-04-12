from __future__ import annotations

# Curated Material Symbols for cost type selection in UI.
COST_TYPE_ICON_OPTIONS: list[tuple[str, str]] = [
    ("Material", "inventory_2"),
    ("Software", "computer"),
    ("Miete", "home_work"),
    ("Werbung", "campaign"),
    ("Reisen", "flight"),
    ("Weiterbildung", "school"),
    ("Sonstiges", "category"),
    ("Werkzeug", "handyman"),
    ("Druck", "print"),
    ("Versand", "local_shipping"),
    ("Telefon", "call"),
    ("Internet", "wifi"),
    ("Strom", "bolt"),
    ("Verpflegung", "restaurant"),
    ("Bahn", "train"),
    ("Auto", "directions_car"),
    ("Buchhaltung", "calculate"),
    ("Bank", "account_balance"),
    ("Messe", "storefront"),
    ("Hosting", "dns"),
    ("Cloud", "cloud"),
    ("Versicherung", "health_and_safety"),
    ("Kunstbedarf", "palette"),
    ("Foto", "photo_camera"),
    ("Vertrag", "description"),
]

COST_TYPE_ICONS: list[str] = [icon for _, icon in COST_TYPE_ICON_OPTIONS]
DEFAULT_COST_TYPE_ICON: str = COST_TYPE_ICON_OPTIONS[0][1]

DEFAULT_COST_TYPES: list[tuple[str, str]] = [
    ("Material", "inventory_2"),
    ("Software", "computer"),
    ("Miete", "home_work"),
    ("Werbung", "campaign"),
    ("Reisen", "flight"),
    ("Weiterbildung", "school"),
    ("Sonstiges", "category"),
]

CONTACT_CATEGORY_ICON_OPTIONS: list[tuple[str, str]] = [
    ("Interessent / Kunde", "handshake"),
    ("Veranstalter", "event"),
    ("Presse", "article"),
    ("Förderung / Institution", "account_balance"),
    ("Sonstiges", "badge"),
]

CONTACT_CATEGORY_ICONS: list[str] = [icon for _, icon in CONTACT_CATEGORY_ICON_OPTIONS]
DEFAULT_CONTACT_CATEGORY_ICON: str = CONTACT_CATEGORY_ICON_OPTIONS[0][1]
DEFAULT_CONTACT_CATEGORY_NAME: str = "Interessent / Kunde"

DEFAULT_CONTACT_CATEGORIES: list[tuple[str, str]] = [
    ("Interessent / Kunde", "handshake"),
    ("Veranstalter", "event"),
    ("Presse", "article"),
    ("Förderung / Institution", "account_balance"),
    ("Sonstiges", "badge"),
]

DEFAULT_COST_AREA_ICON: str = "widgets"
DEFAULT_HIDDEN_COST_AREA_NAME: str = "Allgemeine Ausgabe"
DEFAULT_SUBCATEGORY_NAME: str = "Allgemein"

DEFAULT_COST_AREAS: list[tuple[str, str]] = [
    (DEFAULT_HIDDEN_COST_AREA_NAME, DEFAULT_COST_AREA_ICON),
]


def default_subcategory_name_for_cost_type(cost_type_name: str) -> str:
    name = (cost_type_name or "").strip() or "Kostenkategorie"
    return f"{name} ({DEFAULT_SUBCATEGORY_NAME})"
