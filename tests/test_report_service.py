from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

from belegmanager.models import CostAllocation, CostSubcategory, CostType, Receipt
from belegmanager.services.report_service import ReportService


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _build_service(tmp_path: Path) -> ReportService:
    db_path = tmp_path / "report-test.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        material = CostType(name="Material", icon="inventory_2", active=True)
        software = CostType(name="Software", icon="computer", active=True)
        session.add(material)
        session.add(software)
        session.flush()

        material_sub = CostSubcategory(
            cost_type_id=material.id or 0,
            name="Material (Allgemein)",
            is_system_default=True,
            active=True,
            archived_with_parent=False,
        )
        software_sub = CostSubcategory(
            cost_type_id=software.id or 0,
            name="Software (Allgemein)",
            is_system_default=True,
            active=True,
            archived_with_parent=False,
        )
        session.add(material_sub)
        session.add(software_sub)
        session.flush()

        valid_invoice = Receipt(
            original_filename="r1.pdf",
            archive_path="/tmp/r1.pdf",
            doc_date=date(2026, 1, 10),
            amount_gross_cents=10000,
            vat_rate_percent=19.0,
            amount_net_cents=8403,
            document_type="invoice",
            created_at=_utc_now(),
            updated_at=_utc_now(),
        )
        valid_credit_note = Receipt(
            original_filename="r2.pdf",
            archive_path="/tmp/r2.pdf",
            doc_date=date(2026, 1, 12),
            amount_gross_cents=-2000,
            vat_rate_percent=19.0,
            amount_net_cents=-1681,
            document_type="credit_note",
            created_at=_utc_now(),
            updated_at=_utc_now(),
        )
        deleted_receipt = Receipt(
            original_filename="r3.pdf",
            archive_path="/tmp/r3.pdf",
            doc_date=date(2026, 1, 13),
            amount_gross_cents=5000,
            vat_rate_percent=19.0,
            amount_net_cents=4202,
            document_type="invoice",
            deleted_at=_utc_now(),
            created_at=_utc_now(),
            updated_at=_utc_now(),
        )
        missing_date = Receipt(
            original_filename="r4.pdf",
            archive_path="/tmp/r4.pdf",
            doc_date=None,
            amount_gross_cents=3000,
            vat_rate_percent=19.0,
            amount_net_cents=2521,
            document_type="invoice",
            created_at=_utc_now(),
            updated_at=_utc_now(),
        )
        mismatched_alloc_sum = Receipt(
            original_filename="r5.pdf",
            archive_path="/tmp/r5.pdf",
            doc_date=date(2026, 1, 14),
            amount_gross_cents=9000,
            vat_rate_percent=19.0,
            amount_net_cents=7563,
            document_type="invoice",
            created_at=_utc_now(),
            updated_at=_utc_now(),
        )
        session.add(valid_invoice)
        session.add(valid_credit_note)
        session.add(deleted_receipt)
        session.add(missing_date)
        session.add(mismatched_alloc_sum)
        session.flush()

        session.add(
            CostAllocation(
                receipt_id=valid_invoice.id or 0,
                cost_type_id=material.id or 0,
                cost_subcategory_id=material_sub.id or 0,
                amount_cents=6000,
                position=1,
                created_at=_utc_now(),
                updated_at=_utc_now(),
            )
        )
        session.add(
            CostAllocation(
                receipt_id=valid_invoice.id or 0,
                cost_type_id=software.id or 0,
                cost_subcategory_id=software_sub.id or 0,
                amount_cents=4000,
                position=2,
                created_at=_utc_now(),
                updated_at=_utc_now(),
            )
        )
        session.add(
            CostAllocation(
                receipt_id=valid_credit_note.id or 0,
                cost_type_id=material.id or 0,
                cost_subcategory_id=material_sub.id or 0,
                amount_cents=-2000,
                position=1,
                created_at=_utc_now(),
                updated_at=_utc_now(),
            )
        )
        session.add(
            CostAllocation(
                receipt_id=deleted_receipt.id or 0,
                cost_type_id=material.id or 0,
                cost_subcategory_id=material_sub.id or 0,
                amount_cents=5000,
                position=1,
                created_at=_utc_now(),
                updated_at=_utc_now(),
            )
        )
        session.add(
            CostAllocation(
                receipt_id=missing_date.id or 0,
                cost_type_id=software.id or 0,
                cost_subcategory_id=software_sub.id or 0,
                amount_cents=3000,
                position=1,
                created_at=_utc_now(),
                updated_at=_utc_now(),
            )
        )
        session.add(
            CostAllocation(
                receipt_id=mismatched_alloc_sum.id or 0,
                cost_type_id=software.id or 0,
                cost_subcategory_id=software_sub.id or 0,
                amount_cents=6000,
                position=1,
                created_at=_utc_now(),
                updated_at=_utc_now(),
            )
        )
        session.commit()

    return ReportService(db_engine=engine)


def test_build_summary_uses_only_valid_active_receipts(tmp_path: Path) -> None:
    service = _build_service(tmp_path)
    summary = service.build_summary(date(2026, 1, 1), date(2026, 1, 31))

    assert summary.receipt_count == 2
    assert summary.overall_total_cents == 8000

    by_name = {row.cost_type_name: row.total_cents for row in summary.totals_by_cost_type}
    assert by_name["Material"] == 4000
    assert by_name["Software"] == 4000


def test_build_subcategory_breakdown_respects_date_range(tmp_path: Path) -> None:
    service = _build_service(tmp_path)
    summary = service.build_summary(date(2026, 1, 1), date(2026, 1, 11))
    material_id = next(row.cost_type_id for row in summary.totals_by_cost_type if row.cost_type_name == "Material")

    breakdown = service.build_subcategory_breakdown(
        date_from=date(2026, 1, 1),
        date_to=date(2026, 1, 11),
        cost_type_id=material_id,
    )
    assert len(breakdown) == 1
    assert breakdown[0].total_cents == 6000
