from __future__ import annotations

from datetime import date

from sqlalchemy.pool import StaticPool
import pytest
from sqlmodel import SQLModel, Session, create_engine, select

from belegmanager.constants import DEFAULT_HIDDEN_COST_AREA_NAME
from belegmanager.models import CostAllocation, CostArea, CostSubcategory, CostType, Receipt, Supplier
from belegmanager.schemas import AllocationInput, ReceiptSaveInput
from belegmanager.services.receipt_service import ReceiptService


def _build_service(monkeypatch: pytest.MonkeyPatch) -> tuple[ReceiptService, object]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return ReceiptService(db_engine=engine), engine


def test_update_metadata_rejects_unknown_supplier_id(monkeypatch: pytest.MonkeyPatch) -> None:
    service, engine = _build_service(monkeypatch)
    with Session(engine) as session:
        receipt = Receipt(original_filename="beleg.pdf", archive_path="/tmp/beleg.pdf")
        session.add(receipt)
        session.commit()
        session.refresh(receipt)

    assert receipt.id is not None
    with pytest.raises(ValueError, match="Anbieter nicht gefunden"):
        service.update_metadata(
            receipt_id=receipt.id,
            doc_date=None,
            supplier_id=999_999,
            amount_gross_cents=1000,
            vat_rate_percent=19.0,
            document_type="invoice",
        )


def test_update_metadata_accepts_existing_supplier_id(monkeypatch: pytest.MonkeyPatch) -> None:
    service, engine = _build_service(monkeypatch)
    with Session(engine) as session:
        supplier = Supplier(name="Studio Shop", active=True)
        receipt = Receipt(original_filename="beleg.pdf", archive_path="/tmp/beleg.pdf")
        session.add(supplier)
        session.add(receipt)
        session.commit()
        session.refresh(supplier)
        session.refresh(receipt)

    assert supplier.id is not None
    assert receipt.id is not None

    service.update_metadata(
        receipt_id=receipt.id,
        doc_date=None,
        supplier_id=supplier.id,
        amount_gross_cents=1000,
        vat_rate_percent=19.0,
        document_type="invoice",
    )

    with Session(engine) as session:
        updated = session.get(Receipt, receipt.id)
        assert updated is not None
        assert updated.supplier_id == supplier.id


def test_update_metadata_persists_optional_notes(monkeypatch: pytest.MonkeyPatch) -> None:
    service, engine = _build_service(monkeypatch)
    with Session(engine) as session:
        receipt = Receipt(original_filename="beleg.pdf", archive_path="/tmp/beleg.pdf")
        session.add(receipt)
        session.commit()
        session.refresh(receipt)

    assert receipt.id is not None

    service.update_metadata(
        receipt_id=receipt.id,
        doc_date=None,
        amount_gross_cents=1000,
        vat_rate_percent=19.0,
        notes="  Material für Bühnenbild\nzweite Charge  ",
        document_type="invoice",
    )

    with Session(engine) as session:
        updated = session.get(Receipt, receipt.id)
        assert updated is not None
        assert updated.notes == "Material für Bühnenbild\nzweite Charge"

    service.update_metadata(
        receipt_id=receipt.id,
        doc_date=None,
        amount_gross_cents=1000,
        vat_rate_percent=19.0,
        notes="   ",
        document_type="invoice",
    )

    with Session(engine) as session:
        updated = session.get(Receipt, receipt.id)
        assert updated is not None
        assert updated.notes is None


def test_save_detail_persists_incomplete_allocations_as_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    service, engine = _build_service(monkeypatch)
    with Session(engine) as session:
        supplier = Supplier(name="Studio Shop", active=True)
        default_area = CostArea(name=DEFAULT_HIDDEN_COST_AREA_NAME, color="#4d96ff", icon="widgets", active=True)
        cost_type = CostType(name="Material", color="#ff9f1c", icon="inventory_2", active=True)
        receipt = Receipt(original_filename="beleg.pdf", archive_path="/tmp/beleg.pdf")
        session.add(supplier)
        session.add(default_area)
        session.add(cost_type)
        session.add(receipt)
        session.commit()
        session.refresh(supplier)
        session.refresh(cost_type)
        session.refresh(receipt)
        default_area_id = default_area.id

    assert supplier.id is not None
    assert cost_type.id is not None
    assert receipt.id is not None

    result = service.save_detail(
        receipt.id,
        ReceiptSaveInput(
            doc_date=None,
            supplier_id=supplier.id,
            amount_gross_cents=1000,
            vat_rate_percent=None,
            amount_net_cents=None,
            notes="Entwurf",
            document_type="invoice",
            allocations=[
                AllocationInput(
                    cost_type_id=cost_type.id,
                    cost_subcategory_id=None,
                    project_id=None,
                    cost_area_id=None,
                    amount_cents=1000,
                    position=1,
                )
            ],
        ),
    )

    assert result.is_complete is False
    assert "USt-Satz" in result.missing_fields
    assert "Unterkategorie" in result.missing_fields
    with Session(engine) as session:
        updated = session.get(Receipt, receipt.id)
        allocations = list(session.exec(select(CostAllocation).where(CostAllocation.receipt_id == receipt.id)).all())
        assert updated is not None
        assert updated.amount_net_cents is None
        assert len(allocations) == 1
        assert allocations[0].status == "draft"
        assert allocations[0].cost_area_id == default_area_id


def test_save_detail_promotes_complete_allocations_to_posted(monkeypatch: pytest.MonkeyPatch) -> None:
    service, engine = _build_service(monkeypatch)
    with Session(engine) as session:
        supplier = Supplier(name="Studio Shop", active=True)
        default_area = CostArea(name=DEFAULT_HIDDEN_COST_AREA_NAME, color="#4d96ff", icon="widgets", active=True)
        cost_type = CostType(name="Material", color="#ff9f1c", icon="inventory_2", active=True)
        session.add(supplier)
        session.add(default_area)
        session.add(cost_type)
        session.flush()
        subcategory = CostSubcategory(
            cost_type_id=cost_type.id or 0,
            name="Material (Allgemein)",
            is_system_default=True,
            active=True,
            archived_with_parent=False,
        )
        receipt = Receipt(original_filename="beleg.pdf", archive_path="/tmp/beleg.pdf")
        session.add(subcategory)
        session.add(receipt)
        session.commit()
        session.refresh(supplier)
        session.refresh(cost_type)
        session.refresh(subcategory)
        session.refresh(receipt)

    assert supplier.id is not None
    assert cost_type.id is not None
    assert subcategory.id is not None
    assert receipt.id is not None

    draft_result = service.save_detail(
        receipt.id,
        ReceiptSaveInput(
            doc_date=None,
            supplier_id=supplier.id,
            amount_gross_cents=1000,
            vat_rate_percent=None,
            amount_net_cents=None,
            notes="Entwurf",
            document_type="invoice",
            allocations=[
                AllocationInput(
                    cost_type_id=cost_type.id,
                    cost_subcategory_id=None,
                    project_id=None,
                    cost_area_id=None,
                    amount_cents=1000,
                    position=1,
                )
            ],
        ),
    )
    assert draft_result.is_complete is False

    posted_result = service.save_detail(
        receipt.id,
        ReceiptSaveInput(
            doc_date=date(2026, 1, 10),
            supplier_id=supplier.id,
            amount_gross_cents=1000,
            vat_rate_percent=19.0,
            amount_net_cents=None,
            notes="Fertig",
            document_type="invoice",
            allocations=[
                AllocationInput(
                    cost_type_id=cost_type.id,
                    cost_subcategory_id=subcategory.id,
                    project_id=None,
                    cost_area_id=None,
                    amount_cents=1000,
                    position=1,
                )
            ],
        ),
    )

    assert posted_result.is_complete is True
    with Session(engine) as session:
        updated = session.get(Receipt, receipt.id)
        allocations = list(session.exec(select(CostAllocation).where(CostAllocation.receipt_id == receipt.id)).all())
        assert updated is not None
        assert updated.amount_net_cents is not None
        assert len(allocations) == 1
        assert allocations[0].status == "posted"
