from belegmanager.ui.pages import _extract_row_id


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
