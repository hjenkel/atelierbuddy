from __future__ import annotations

from sqlmodel import SQLModel, Session, create_engine, select

from belegmanager.models import CostAllocation, CostSubcategory, CostType, Project, Receipt, Supplier
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
        service.create_or_update_project(name="x", active=True, price_cents=None, created_on=None)
    except ValueError as exc:
        assert "zwischen" in str(exc)
    else:
        raise AssertionError("expected ValueError for invalid name length")


def test_project_create_and_update_persists_price() -> None:
    service, engine = _build_service()
    project, created = service.create_or_update_project(
        name="Album Artwork",
        active=True,
        price_cents=125000,
        created_on=None,
    )
    assert created
    assert project.id is not None
    assert project.price_cents == 125000

    updated = service.update_project(
        project_id=project.id,
        name="Album Artwork",
        active=False,
        price_cents=149900,
        created_on=None,
    )
    assert updated.price_cents == 149900
    assert updated.active is False

    with Session(engine) as session:
        persisted = session.get(Project, project.id)
        assert persisted is not None
        assert persisted.price_cents == 149900
        assert persisted.active is False


def test_delete_project_rejects_existing_allocations() -> None:
    service, engine = _build_service()
    category, _ = service.create_or_update_cost_type(name="Material", icon="category")
    project, _ = service.create_or_update_project(
        name="Buehnenbild",
        active=True,
        price_cents=99000,
        created_on=None,
    )
    assert category.id is not None
    assert project.id is not None

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
                project_id=project.id,
                amount_cents=1000,
                position=1,
            )
        )
        session.commit()

    try:
        service.delete_project(project_id=project.id)
    except ValueError as exc:
        assert "Bitte entferne zuerst alle Zuordnungen manuell" in str(exc)
    else:
        raise AssertionError("expected ValueError for used project")

    with Session(engine) as session:
        assert session.get(Project, project.id) is not None


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
