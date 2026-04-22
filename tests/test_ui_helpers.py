from decimal import Decimal

from belegmanager.models import Contact
from belegmanager.services.order_service import order_item_total_cents
from belegmanager.ui.pages import (
    _build_staged_upload_entries,
    _common_project_id_from_rows,
    _contact_display_name_from_values,
    _contact_sort_key,
    _extract_model_value,
    _extract_row_id,
    _normalize_money_input,
    _normalize_quantity_input,
    _parse_money_to_cents,
    _parse_quantity,
    _uses_position_project_mode,
)


def test_extract_row_id_accepts_int_and_numeric_str() -> None:
    assert _extract_row_id({"id": 7}) == 7
    assert _extract_row_id({"id": "7"}) == 7
    assert _extract_row_id({"row": {"id": 11}}) == 11
    assert _extract_row_id({"row": {"id": "11"}}) == 11


def test_extract_row_id_accepts_row_click_list_payload() -> None:
    assert _extract_row_id([{"type": "click"}, {"id": 21, "supplier": "A"}, 0]) == 21
    assert _extract_row_id([{"type": "click"}, {"row": {"id": "22"}}, 0]) == 22


def test_extract_row_id_ignores_numeric_event_fields_and_uses_row_payload() -> None:
    payload = [{"detail": 1, "button": 0, "pageX": 834}, {"id": 47, "name": "Beleg"}, 3]
    assert _extract_row_id(payload) == 47


def test_extract_row_id_accepts_event_args_wrapper() -> None:
    class Wrapped:
        def __init__(self, payload: object) -> None:
            self.args = payload

    wrapped = Wrapped([{"type": "click"}, {"id": "33"}, 1])
    assert _extract_row_id(wrapped) == 33


def test_extract_row_id_returns_none_for_invalid_payload() -> None:
    assert _extract_row_id({"id": "abc"}) is None
    assert _extract_row_id({"row": {"id": "abc"}}) is None
    assert _extract_row_id({"row": {}}) is None
    assert _extract_row_id([{"detail": 1, "button": 0}, {"supplier": "X"}, 0]) is None


def test_extract_model_value_accepts_quasar_model_value_keys() -> None:
    assert _extract_model_value({"value": "2026-04-22"}) == "2026-04-22"
    assert _extract_model_value({"modelValue": "2026-04-23"}) == "2026-04-23"
    assert _extract_model_value({"model-value": "2026-04-24"}) == "2026-04-24"


def test_contact_display_name_from_values_joins_present_parts() -> None:
    assert _contact_display_name_from_values("Ada", "Lovelace") == "Ada Lovelace"
    assert _contact_display_name_from_values("Ada", "") == "Ada"
    assert _contact_display_name_from_values("", "Lovelace") == "Lovelace"


def test_contact_sort_key_prefers_family_name_then_given_name() -> None:
    primary = Contact(given_name="Mila", family_name="Stern", contact_category_id=1)
    fallback = Contact(given_name="Alex", family_name=None, contact_category_id=1)

    assert _contact_sort_key(primary) == ("stern", "mila", "")
    assert _contact_sort_key(fallback) == ("alex", "", "")


def test_order_value_helpers_keep_10_eur_times_1_at_10_eur() -> None:
    quantity = _parse_quantity("1")
    unit_price_cents = _parse_money_to_cents("10", allow_negative=True)
    assert quantity == Decimal("1.000")
    assert unit_price_cents == 1000
    assert order_item_total_cents(quantity, unit_price_cents) == 1000


def test_order_value_helpers_normalize_inputs_to_canonical_format() -> None:
    assert _normalize_quantity_input("1,00") == "1"
    assert _normalize_money_input("10", allow_negative=True) == "10,00"


def test_project_mode_helpers_detect_common_and_mixed_projects() -> None:
    same_project_rows = [{"project_id": 3}, {"project_id": 3}]
    mixed_project_rows = [{"project_id": 3}, {"project_id": None}]

    assert _common_project_id_from_rows(same_project_rows) == 3
    assert _uses_position_project_mode(same_project_rows) is False
    assert _common_project_id_from_rows(mixed_project_rows) is None
    assert _uses_position_project_mode(mixed_project_rows) is True


def test_build_staged_upload_entries_keeps_supported_files() -> None:
    class FakeUpload:
        def __init__(self, name: str, size_value: int) -> None:
            self.name = name
            self._size_value = size_value

        def size(self) -> int:
            return self._size_value

    entries, skipped = _build_staged_upload_entries(
        [FakeUpload("beleg.pdf", 2048), FakeUpload("scan.jpg", 512)]
    )

    assert skipped == 0
    assert [entry["name"] for entry in entries] == ["beleg.pdf", "scan.jpg"]
    assert [entry["size"] for entry in entries] == [2048, 512]
    assert all(entry["id"] for entry in entries)


def test_build_staged_upload_entries_skips_unsupported_and_handles_size_errors() -> None:
    class BrokenSizeUpload:
        def __init__(self, name: str) -> None:
            self.name = name

        def size(self) -> int:
            raise RuntimeError("broken")

    entries, skipped = _build_staged_upload_entries(
        [BrokenSizeUpload("scan.png"), BrokenSizeUpload("notiz.txt")]
    )

    assert skipped == 1
    assert len(entries) == 1
    assert entries[0]["name"] == "scan.png"
    assert entries[0]["size"] == 0
