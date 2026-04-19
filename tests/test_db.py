from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import Session, create_engine

from belegmanager import db
from belegmanager.config import Settings


def _temp_settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    archive_dir = data_dir / "archive"
    return Settings(
        root_dir=tmp_path,
        data_dir=data_dir,
        assets_dir=tmp_path / "assets",
        db_path=data_dir / "belegmanager.db",
        archive_dir=archive_dir,
        originals_dir=archive_dir / "originals",
        normalized_dir=archive_dir / "normalized",
        ocr_dir=archive_dir / "ocr",
        thumbs_dir=archive_dir / "thumbs",
        order_invoices_dir=archive_dir / "order_invoices",
        invoice_assets_dir=archive_dir / "invoice_assets",
        invoice_logos_dir=archive_dir / "invoice_assets" / "logos",
        works_cover_dir=archive_dir / "work_covers",
    )


def _configure_temp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Settings, object]:
    settings = _temp_settings(tmp_path)
    settings.ensure_dirs()
    engine = create_engine(f"sqlite:///{settings.db_path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(db, "settings", settings)
    monkeypatch.setattr(db, "engine", engine)
    return settings, engine


def test_init_db_creates_migration_metadata_and_fts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _, engine = _configure_temp_db(monkeypatch, tmp_path)

    db.init_db()

    with Session(engine) as session:
        migration_ids = [str(row[0]) for row in session.exec(text("SELECT migration_id FROM schema_migration")).all()]
        fts_table = session.exec(
            text("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'receipt_fts'")
        ).one()

    assert sorted(migration_ids) == [migration.migration_id for migration in db.MIGRATIONS]
    assert fts_table[0] == "receipt_fts"


def test_init_db_migrates_legacy_schema_without_touching_archive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings, engine = _configure_temp_db(monkeypatch, tmp_path)
    archive_probe = settings.archive_dir / "keep.txt"
    archive_probe.write_text("preserve me", encoding="utf-8")

    with Session(engine) as session:
        session.exec(
            text(
                """
                CREATE TABLE receipt (
                    id INTEGER PRIMARY KEY,
                    original_filename TEXT,
                    archive_path TEXT,
                    status TEXT
                )
                """
            )
        )
        session.exec(text("CREATE TABLE cost_type (id INTEGER PRIMARY KEY, name TEXT)"))
        session.exec(text("CREATE TABLE cost_area (id INTEGER PRIMARY KEY, name TEXT)"))
        session.exec(text("CREATE TABLE project (id INTEGER PRIMARY KEY, name TEXT)"))
        session.exec(
            text(
                """
                CREATE TABLE cost_subcategory (
                    id INTEGER PRIMARY KEY,
                    cost_type_id INTEGER,
                    name TEXT,
                    is_system_default BOOLEAN,
                    active BOOLEAN
                )
                """
            )
        )
        session.exec(
            text(
                """
                CREATE TABLE cost_allocation (
                    id INTEGER PRIMARY KEY,
                    receipt_id INTEGER,
                    cost_type_id INTEGER,
                    project_id INTEGER,
                    cost_area_id INTEGER,
                    amount_cents INTEGER,
                    position INTEGER
                )
                """
            )
        )
        session.exec(text("CREATE TABLE contact_category (id INTEGER PRIMARY KEY, name TEXT)"))
        session.exec(
            text(
                """
                CREATE TABLE contact (
                    id INTEGER PRIMARY KEY,
                    given_name TEXT,
                    family_name TEXT,
                    organisation TEXT,
                    email TEXT,
                    phone TEXT,
                    mobile TEXT,
                    primary_link TEXT,
                    city TEXT,
                    notes TEXT,
                    contact_category_id INTEGER,
                    created_at TIMESTAMP,
                    updated_at TIMESTAMP
                )
                """
            )
        )
        session.exec(
            text(
                """
                CREATE TABLE sales_order (
                    id INTEGER PRIMARY KEY,
                    internal_number TEXT,
                    contact_id INTEGER,
                    sale_date TEXT,
                    invoice_date TEXT,
                    invoice_number TEXT,
                    deleted_at TIMESTAMP
                )
                """
            )
        )
        session.exec(
            text(
                """
                INSERT INTO contact (
                    id, given_name, family_name, organisation, email, phone, mobile, primary_link, city, notes,
                    contact_category_id, created_at, updated_at
                ) VALUES (
                    1, 'Mira', 'Stern', NULL, NULL, NULL, NULL, NULL, 'Berlin', NULL, 1, NULL, NULL
                )
                """
            )
        )
        session.commit()

    db.init_db()

    with Session(engine) as session:
        contact_columns = {str(row[1]) for row in session.exec(text("PRAGMA table_info(contact)")).all()}
        order_columns = {str(row[1]) for row in session.exec(text("PRAGMA table_info(sales_order)")).all()}
        profile_columns = {str(row[1]) for row in session.exec(text("PRAGMA table_info(invoice_profile)")).all()}
        allocation_columns = {
            str(row[1]): row
            for row in session.exec(text("PRAGMA table_info(cost_allocation)")).all()
        }
        migrated_country = session.exec(text("SELECT country FROM contact WHERE id = 1")).one()
        migration_count = session.exec(text("SELECT COUNT(*) FROM schema_migration")).one()

    assert {"street", "house_number", "address_extra", "postal_code", "country"}.issubset(contact_columns)
    assert {"invoice_document_updated_at", "invoice_document_source"}.issubset(order_columns)
    assert {"display_name", "tax_id_type", "payment_term_days", "logo_path"}.issubset(profile_columns)
    assert "status" in allocation_columns
    assert int(allocation_columns["cost_type_id"][3]) == 0
    assert migrated_country[0] == "DE"
    assert migration_count[0] == len(db.MIGRATIONS)
    assert archive_probe.read_text(encoding="utf-8") == "preserve me"


def test_init_db_is_idempotent_for_applied_migrations(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _, engine = _configure_temp_db(monkeypatch, tmp_path)

    db.init_db()
    db.init_db()

    with Session(engine) as session:
        migration_count = session.exec(text("SELECT COUNT(*) FROM schema_migration")).one()

    assert migration_count[0] == len(db.MIGRATIONS)


def test_init_db_blocks_on_unmigrated_legacy_schema(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings, engine = _configure_temp_db(monkeypatch, tmp_path)
    archive_probe = settings.archive_dir / "keep.txt"
    archive_probe.write_text("do not delete", encoding="utf-8")

    with Session(engine) as session:
        session.exec(
            text(
                """
                CREATE TABLE contact (
                    id INTEGER PRIMARY KEY,
                    given_name TEXT,
                    family_name TEXT,
                    organisation TEXT,
                    email TEXT,
                    phone TEXT,
                    mobile TEXT,
                    primary_link TEXT,
                    city TEXT,
                    notes TEXT,
                    contact_category_id INTEGER
                )
                """
            )
        )
        session.commit()

    monkeypatch.setattr(db, "MIGRATIONS", ())

    with pytest.raises(db.DatabaseMigrationError, match="Schema validation failed for table 'contact'"):
        db.init_db()

    assert settings.db_path.exists()
    assert archive_probe.read_text(encoding="utf-8") == "do not delete"
