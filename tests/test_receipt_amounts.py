from belegmanager.services.receipt_service import ReceiptService
from belegmanager.ui.pages import _allocation_total_and_diff_cents, _compute_net_cents, _parse_money_to_cents


def test_calculate_net_cents_with_default_rounding() -> None:
    service = ReceiptService()

    assert service._calculate_net_cents(11900, 19.0) == 10000
    assert service._calculate_net_cents(10000, 0.0) == 10000
    assert service._calculate_net_cents(199, 19.0) == 167


def test_parse_money_to_cents_supports_de_and_en_formats() -> None:
    assert _parse_money_to_cents("100") == 10000
    assert _parse_money_to_cents("100,00") == 10000
    assert _parse_money_to_cents("100.00") == 10000
    assert _parse_money_to_cents("1.234") == 123400
    assert _parse_money_to_cents("1,234") == 123400
    assert _parse_money_to_cents("12.34") == 1234
    assert _parse_money_to_cents("12,34") == 1234
    assert _parse_money_to_cents("1.234,56") == 123456
    assert _parse_money_to_cents("1,234.56") == 123456


def test_parse_money_to_cents_supports_numeric_values() -> None:
    assert _parse_money_to_cents(100) == 10000
    assert _parse_money_to_cents(50.0) == 5000
    assert _parse_money_to_cents(12.34) == 1234


def test_parse_money_to_cents_supports_negative_values_when_enabled() -> None:
    assert _parse_money_to_cents("-100", allow_negative=True) == -10000
    assert _parse_money_to_cents("-100,50", allow_negative=True) == -10050
    assert _parse_money_to_cents("-1.234,56", allow_negative=True) == -123456


def test_allocation_total_and_diff_uses_consistent_amount_parsing() -> None:
    total_cents, diff_cents = _allocation_total_and_diff_cents(10000, ["50"])
    assert total_cents == 5000
    assert diff_cents == 5000

    total_cents, diff_cents = _allocation_total_and_diff_cents(10000, ["60,00", "50"])
    assert total_cents == 11000
    assert diff_cents == -1000

    total_cents, diff_cents = _allocation_total_and_diff_cents(10000, ["50,00", "50"])
    assert total_cents == 10000
    assert diff_cents == 0


def test_allocation_total_and_diff_handles_negative_allocations() -> None:
    total_cents, diff_cents = _allocation_total_and_diff_cents(-10000, ["-50"], allow_negative=True)
    assert total_cents == -5000
    assert diff_cents == -5000

    total_cents, diff_cents = _allocation_total_and_diff_cents(-10000, ["-60,00", "-40"], allow_negative=True)
    assert total_cents == -10000
    assert diff_cents == 0


def test_net_preview_uses_division_formula() -> None:
    assert _compute_net_cents(_parse_money_to_cents("100,00"), 19.0) == 8403
    assert _compute_net_cents(_parse_money_to_cents("119,00"), 19.0) == 10000
