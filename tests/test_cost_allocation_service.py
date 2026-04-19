from datetime import datetime, timezone

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from belegmanager.constants import DEFAULT_HIDDEN_COST_AREA_NAME
from belegmanager.models import CostAllocation, CostArea, CostSubcategory, CostType, Receipt
from belegmanager.schemas import AllocationInput
from belegmanager.services.cost_allocation_service import CostAllocationService


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _build_service(monkeypatch: pytest.MonkeyPatch) -> tuple[CostAllocationService, object]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("belegmanager.services.cost_allocation_service.engine", engine)
    return CostAllocationService(), engine


def _seed_receipt_context(engine: object) -> tuple[int, int, int]:
    with Session(engine) as session:
        default_area = CostArea(name=DEFAULT_HIDDEN_COST_AREA_NAME, color="#4d96ff", icon="widgets", active=True)
        cost_type = CostType(name="Material", color="#ff9f1c", icon="inventory_2", active=True)
        session.add(default_area)
        session.add(cost_type)
        session.flush()

        subcategory = CostSubcategory(
            cost_type_id=cost_type.id or 0,
            name="Material (Allgemein)",
            is_system_default=True,
            active=True,
            archived_with_parent=False,
            created_at=_utc_now(),
            updated_at=_utc_now(),
        )
        receipt = Receipt(
            original_filename="beleg.pdf",
            archive_path="/tmp/beleg.pdf",
            amount_gross_cents=10000,
            vat_rate_percent=19.0,
            amount_net_cents=8403,
            document_type="invoice",
            created_at=_utc_now(),
            updated_at=_utc_now(),
        )
        session.add(subcategory)
        session.add(receipt)
        session.commit()
        session.refresh(subcategory)
        session.refresh(receipt)

        assert receipt.id is not None
        assert cost_type.id is not None
        assert subcategory.id is not None
        return receipt.id, cost_type.id, subcategory.id


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
                cost_area_id=3,
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
                    cost_area_id=3,
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


def test_prepare_allocations_keeps_incomplete_rows_and_defaults_cost_area(monkeypatch: pytest.MonkeyPatch) -> None:
    service, engine = _build_service(monkeypatch)
    receipt_id, cost_type_id, subcategory_id = _seed_receipt_context(engine)
    with Session(engine) as session:
        normalized, subcategory_type_ids = service.prepare_allocations(
            session,
            [
                AllocationInput(
                    cost_type_id=None,
                    cost_subcategory_id=None,
                    project_id=None,
                    cost_area_id=None,
                    amount_cents=None,
                    position=1,
                ),
                AllocationInput(
                    cost_type_id=cost_type_id,
                    cost_subcategory_id=subcategory_id,
                    project_id=None,
                    cost_area_id=None,
                    amount_cents=10000,
                    position=2,
                ),
            ],
        )

    assert receipt_id > 0
    assert len(normalized) == 1
    assert normalized[0].cost_area_id is not None
    assert normalized[0].cost_type_id == cost_type_id
    assert subcategory_type_ids[subcategory_id] == cost_type_id


def test_save_allocations_persists_posted_status(monkeypatch: pytest.MonkeyPatch) -> None:
    service, engine = _build_service(monkeypatch)
    receipt_id, cost_type_id, subcategory_id = _seed_receipt_context(engine)

    service.save_allocations(
        receipt_id=receipt_id,
        allocations=[
            AllocationInput(
                cost_type_id=cost_type_id,
                cost_subcategory_id=subcategory_id,
                project_id=None,
                cost_area_id=None,
                amount_cents=10000,
                position=1,
            )
        ],
    )

    with Session(engine) as session:
        allocations = list(session.exec(select(CostAllocation).where(CostAllocation.receipt_id == receipt_id)).all())
        assert len(allocations) == 1
        assert allocations[0].amount_cents == 10000
        assert allocations[0].status == "posted"
