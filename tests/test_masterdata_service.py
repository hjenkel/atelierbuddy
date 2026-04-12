from __future__ import annotations

from sqlmodel import SQLModel, Session, create_engine, select

from belegmanager.constants import DEFAULT_CONTACT_CATEGORY_NAME
from belegmanager.db import _seed_defaults
from belegmanager.models import CostAllocation, CostSubcategory, CostType, Project, Receipt, Supplier
from belegmanager.services.masterdata_service import MasterDataService
from belegmanager.models import Contact, ContactCategory


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


def test_seed_defaults_adds_contact_categories() -> None:
    service, engine = _build_service()
    del service
    with Session(engine) as session:
        _seed_defaults(session)
        categories = list(session.exec(select(ContactCategory).order_by(ContactCategory.name)).all())

    assert [item.name for item in categories] == [
        "Förderung / Institution",
        "Interessent / Kunde",
        "Presse",
        "Sonstiges",
        "Veranstalter",
    ]


def test_name_validation_rejects_short_values() -> None:
    service, _ = _build_service()
    try:
        service.create_or_update_project(name="x", active=True, price_cents=None, created_on=None)
    except ValueError as exc:
        assert "zwischen" in str(exc)
    else:
        raise AssertionError("expected ValueError for invalid name length")


def test_contact_category_create_and_case_insensitive_uniqueness() -> None:
    service, _ = _build_service()
    category, created = service.create_or_update_contact_category(name="Presse", icon="article")
    assert created
    assert category.id is not None

    same_category, created_again = service.create_or_update_contact_category(name="presse", icon="campaign")
    assert not created_again
    assert same_category.id == category.id
    assert same_category.icon == "campaign"


def test_contact_validation_accepts_given_or_family_name_only() -> None:
    service, _ = _build_service()
    category, _ = service.create_or_update_contact_category(name="Veranstalter", icon="event")

    contact_only_given = service.create_contact(
        given_name="Kim",
        family_name="",
        organisation=None,
        email=None,
        phone=None,
        mobile=None,
        primary_link=None,
        city=None,
        notes=None,
        contact_category_id=category.id or -1,
    )
    assert contact_only_given.given_name == "Kim"
    assert contact_only_given.family_name is None

    contact_only_family = service.create_contact(
        given_name="",
        family_name="Ng",
        organisation=None,
        email=None,
        phone=None,
        mobile=None,
        primary_link=None,
        city=None,
        notes=None,
        contact_category_id=category.id or -1,
    )
    assert contact_only_family.given_name is None
    assert contact_only_family.family_name == "Ng"

    contact_both = service.create_contact(
        given_name="Alex",
        family_name="Meyer",
        organisation=None,
        email=None,
        phone=None,
        mobile=None,
        primary_link=None,
        city=None,
        notes=None,
        contact_category_id=category.id or -1,
    )
    assert contact_both.given_name == "Alex"
    assert contact_both.family_name == "Meyer"


def test_contact_validation_rejects_missing_name_parts() -> None:
    service, _ = _build_service()
    category, _ = service.create_or_update_contact_category(name="Presse", icon="article")
    try:
        service.create_contact(
            given_name="",
            family_name="",
            organisation=None,
            email=None,
            phone=None,
            mobile=None,
            primary_link=None,
            city=None,
            notes=None,
            contact_category_id=category.id or -1,
        )
    except ValueError as exc:
        assert "Mindestens Vorname oder Nachname" in str(exc)
    else:
        raise AssertionError("expected ValueError for missing contact name")


def test_contact_duplicates_are_allowed() -> None:
    service, engine = _build_service()
    category, _ = service.create_or_update_contact_category(name="Interessent / Kunde", icon="handshake")

    first = service.create_contact(
        given_name="Alex",
        family_name="Meyer",
        organisation="Studio Nord",
        email=None,
        phone=None,
        mobile=None,
        primary_link=None,
        city=None,
        notes=None,
        contact_category_id=category.id or -1,
    )
    second = service.create_contact(
        given_name="Alex",
        family_name="Meyer",
        organisation="Studio Süd",
        email=None,
        phone=None,
        mobile=None,
        primary_link=None,
        city=None,
        notes=None,
        contact_category_id=category.id or -1,
    )
    assert first.id is not None
    assert second.id is not None
    assert first.id != second.id

    with Session(engine) as session:
        assert len(list(session.exec(select(Contact)).all())) == 2


def test_contact_category_delete_rejects_used_category() -> None:
    service, engine = _build_service()
    category, _ = service.create_or_update_contact_category(name="Presse", icon="article")
    assert category.id is not None
    service.create_contact(
        given_name="Mila",
        family_name="Stern",
        organisation=None,
        email=None,
        phone=None,
        mobile=None,
        primary_link=None,
        city=None,
        notes=None,
        contact_category_id=category.id,
    )

    try:
        service.delete_contact_category(category_id=category.id)
    except ValueError as exc:
        assert "wird noch verwendet" in str(exc)
    else:
        raise AssertionError("expected ValueError for used contact category")

    with Session(engine) as session:
        assert session.get(ContactCategory, category.id) is not None


def test_contact_category_delete_removes_unused_category() -> None:
    service, engine = _build_service()
    category, _ = service.create_or_update_contact_category(name="Sonstiges", icon="badge")
    assert category.id is not None

    service.delete_contact_category(category_id=category.id)

    with Session(engine) as session:
        assert session.get(ContactCategory, category.id) is None


def test_contact_create_update_delete_roundtrip() -> None:
    service, engine = _build_service()
    with Session(engine) as session:
        _seed_defaults(session)
        default_category = session.exec(
            select(ContactCategory).where(ContactCategory.name == DEFAULT_CONTACT_CATEGORY_NAME)
        ).first()
        assert default_category is not None
        assert default_category.id is not None

    contact = service.create_contact(
        given_name="Jule",
        family_name="Becker",
        organisation="Club West",
        email="jule@example.com",
        phone="040 12345",
        mobile="0170 5555",
        primary_link="https://example.com",
        city="Hamburg",
        notes="Schreibt wegen Booking.",
        contact_category_id=default_category.id,
    )
    assert contact.id is not None

    updated = service.update_contact(
        contact_id=contact.id,
        given_name="Jule",
        family_name="Becker",
        organisation="Club Ost",
        email="j.becker@example.com",
        phone="040 67890",
        mobile="0170 9999",
        primary_link="https://example.org",
        city="Berlin",
        notes="Jetzt bestätigt.",
        contact_category_id=default_category.id,
    )
    assert updated.organisation == "Club Ost"

    service.delete_contact(contact_id=contact.id)
    with Session(engine) as session:
        assert session.get(Contact, contact.id) is None


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
