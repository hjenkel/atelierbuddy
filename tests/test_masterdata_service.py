from __future__ import annotations

from sqlmodel import SQLModel, Session, create_engine, select

from belegmanager.models import CostAllocation, CostSubcategory, CostType, Receipt, Supplier
from belegmanager.services.masterdata_service import MasterDataService


def _build_service() -> tuple[MasterDataService, object]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return MasterDataService(db_engine=engine), engine


def test_supplier_create_and_case_insensitive_uniqueness() -> None:
    service, _ = _build_service()
    supplier, created = service.create_or_update_supplier(name="Studio Shop", active=True)
    assert created
    assert supplier.id is not None

    same_supplier, created_again = service.create_or_update_supplier(name="studio shop", active=False)
    assert not created_again
    assert same_supplier.id == supplier.id
    assert same_supplier.active is False


def test_name_validation_rejects_short_values() -> None:
    service, _ = _build_service()
    try:
        service.create_or_update_project(name="x", active=True, created_on=None)
    except ValueError as exc:
        assert "zwischen" in str(exc)
    else:
        raise AssertionError("expected ValueError for invalid name length")


def test_cost_type_primary_action_deletes_unused_type() -> None:
    service, engine = _build_service()
    category, _ = service.create_or_update_cost_type(name="Material", icon="category")
    assert category.id is not None

    action = service.archive_or_delete_cost_type(category_id=category.id)
    assert action == "deleted"

    with Session(engine) as session:
        assert session.get(CostType, category.id) is None


def test_cost_type_primary_action_archives_used_type() -> None:
    service, engine = _build_service()
    category, _ = service.create_or_update_cost_type(name="Software", icon="memory")
    assert category.id is not None

    with Session(engine) as session:
        subcategory = session.exec(
            select(CostSubcategory).where(CostSubcategory.cost_type_id == category.id)
        ).first()
        assert subcategory is not None
        receipt = Receipt(
            original_filename="beleg.pdf",
            archive_path="/tmp/beleg.pdf",
            amount_gross_cents=1000,
            document_type="invoice",
            status="done",
        )
        session.add(receipt)
        session.flush()
        assert receipt.id is not None
        session.add(
            CostAllocation(
                receipt_id=receipt.id,
                cost_type_id=category.id,
                cost_subcategory_id=subcategory.id,
                amount_cents=1000,
                position=1,
            )
        )
        session.commit()

    action = service.archive_or_delete_cost_type(category_id=category.id)
    assert action == "archived"

    with Session(engine) as session:
        updated = session.get(CostType, category.id)
        assert updated is not None
        assert updated.active is False
