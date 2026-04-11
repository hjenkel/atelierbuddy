from __future__ import annotations

from sqlalchemy.pool import StaticPool
import pytest
from sqlmodel import SQLModel, Session, create_engine

from belegmanager.models import Receipt, Supplier
from belegmanager.services.receipt_service import ReceiptService


def _build_service(monkeypatch: pytest.MonkeyPatch) -> tuple[ReceiptService, object]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("belegmanager.services.receipt_service.engine", engine)
    return ReceiptService(), engine


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
