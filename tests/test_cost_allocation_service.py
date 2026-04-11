from belegmanager.schemas import AllocationInput
from belegmanager.services.cost_allocation_service import CostAllocationService


def test_validate_allocations_payload_accepts_single_standard_allocation() -> None:
    service = CostAllocationService()
    service._validate_allocations_payload(
        allocations=[
            AllocationInput(
                cost_type_id=1,
                cost_subcategory_id=1,
                project_id=2,
                cost_area_id=None,
                amount_cents=10000,
                position=1,
            )
        ],
        gross_cents=10000,
    )


def test_validate_allocations_payload_accepts_optional_project() -> None:
    service = CostAllocationService()
    service._validate_allocations_payload(
        allocations=[
            AllocationInput(
                cost_type_id=1,
                cost_subcategory_id=1,
                project_id=None,
                cost_area_id=None,
                amount_cents=10000,
                position=1,
            )
        ],
        gross_cents=10000,
    )


def test_validate_allocations_payload_rejects_missing_subcategory() -> None:
    service = CostAllocationService()
    try:
        service._validate_allocations_payload(
            allocations=[
                AllocationInput(
                    cost_type_id=1,
                    cost_subcategory_id=0,
                    project_id=None,
                    cost_area_id=None,
                    amount_cents=10000,
                    position=1,
                )
            ],
            gross_cents=10000,
        )
    except ValueError as exc:
        assert "Unterkategorie" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_validate_allocations_payload_rejects_ambiguous_target() -> None:
    service = CostAllocationService()
    try:
        service._validate_allocations_payload(
            allocations=[
                AllocationInput(
                    cost_type_id=1,
                    cost_subcategory_id=1,
                    project_id=2,
                    cost_area_id=3,
                    amount_cents=10000,
                    position=1,
                )
            ],
            gross_cents=10000,
        )
    except ValueError as exc:
        assert "gleichzeitig Projekt und Kostenstelle" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_validate_allocations_payload_rejects_sum_mismatch() -> None:
    service = CostAllocationService()
    try:
        service._validate_allocations_payload(
            allocations=[
                AllocationInput(
                    cost_type_id=1,
                    cost_subcategory_id=1,
                    project_id=2,
                    cost_area_id=None,
                    amount_cents=6000,
                    position=1,
                ),
                AllocationInput(
                    cost_type_id=1,
                    cost_subcategory_id=1,
                    project_id=None,
                    cost_area_id=None,
                    amount_cents=3000,
                    position=2,
                ),
            ],
            gross_cents=10000,
        )
    except ValueError as exc:
        assert "vollständig abdecken" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_validate_allocations_payload_accepts_credit_note_with_negative_amounts() -> None:
    service = CostAllocationService()
    service._validate_allocations_payload(
        allocations=[
            AllocationInput(
                cost_type_id=1,
                cost_subcategory_id=1,
                project_id=2,
                cost_area_id=None,
                amount_cents=-10000,
                position=1,
            )
        ],
        gross_cents=-10000,
        document_type="credit_note",
    )


def test_validate_allocations_payload_rejects_sign_mismatch_for_credit_note() -> None:
    service = CostAllocationService()
    try:
        service._validate_allocations_payload(
            allocations=[
                AllocationInput(
                    cost_type_id=1,
                    cost_subcategory_id=1,
                    project_id=2,
                    cost_area_id=None,
                    amount_cents=10000,
                    position=1,
                )
            ],
            gross_cents=-10000,
            document_type="credit_note",
        )
    except ValueError as exc:
        assert "Gutschrift" in str(exc)
    else:
        raise AssertionError("expected ValueError")
