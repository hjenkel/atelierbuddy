from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
import logging

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine, select

from .config import settings
from .constants import (
    COST_ALLOCATION_STATUS_POSTED,
    DEFAULT_CONTACT_CATEGORIES,
    DEFAULT_CONTACT_CATEGORY_ICON,
    DEFAULT_COST_AREA_ICON,
    DEFAULT_COST_AREAS,
    DEFAULT_COST_TYPES,
    DEFAULT_HIDDEN_COST_AREA_NAME,
    DEFAULT_SUBCATEGORY_NAME,
    default_subcategory_name_for_cost_type,
)
from .fts import init_fts
from .models import Contact, ContactCategory, CostAllocation, CostArea, CostSubcategory, CostType, Order, OrderItem, Receipt
from .receipt_completion import ReceiptCompletionService

LOG = logging.getLogger(__name__)
MIGRATION_TABLE = "schema_migration"

settings.ensure_dirs()

engine = create_engine(
    f"sqlite:///{settings.db_path}",
    connect_args={"check_same_thread": False},
    echo=False,
)


@contextmanager
def session_scope() -> Session:
    with Session(engine) as session:
        yield session


class DatabaseMigrationError(RuntimeError):
    """Raised when the database schema cannot be safely migrated or validated."""


@dataclass(frozen=True)
class MigrationStep:
    migration_id: str
    description: str
    apply: Callable[[Session], None]


def init_db() -> None:
    settings.ensure_dirs()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        _ensure_migration_table(session)
        _apply_migrations(session)
        _validate_schema_state(session)
        init_fts(session)
        _seed_defaults(session)


def _seed_defaults(session: Session) -> None:
    existing_contact_categories = {
        item.name.casefold(): item for item in session.exec(select(ContactCategory).order_by(ContactCategory.name)).all()
    }
    for name, icon in DEFAULT_CONTACT_CATEGORIES:
        existing_item = existing_contact_categories.get(name.casefold())
        if existing_item is None:
            session.add(ContactCategory(name=name, icon=icon))
            continue
        if (existing_item.icon or "").strip() in {"", "label"}:
            existing_item.icon = icon
        session.add(existing_item)

    existing_cost_areas = {
        item.name.casefold(): item for item in session.exec(select(CostArea).order_by(CostArea.name)).all()
    }
    for name, icon in DEFAULT_COST_AREAS:
        existing_item = existing_cost_areas.get(name.casefold())
        if existing_item is None:
            session.add(CostArea(name=name, icon=icon))
            continue
        if (existing_item.icon or "").strip() in {"", "label"}:
            existing_item.icon = icon
        if not existing_item.active:
            existing_item.active = True
        session.add(existing_item)

    # Keep existing legacy "Verwaltung" entries and ensure a fallback icon.
    for item in existing_cost_areas.values():
        if (item.icon or "").strip() == "":
            item.icon = DEFAULT_COST_AREA_ICON
            session.add(item)

    existing_cost_types = {
        item.name.casefold(): item for item in session.exec(select(CostType).order_by(CostType.name)).all()
    }
    for name, icon in DEFAULT_COST_TYPES:
        existing_item = existing_cost_types.get(name.casefold())
        if existing_item is None:
            session.add(CostType(name=name, icon=icon))
            continue
        if existing_item.active is None:
            existing_item.active = True
            session.add(existing_item)
        if (existing_item.icon or "").strip() in {"", "label"}:
            existing_item.icon = icon
            session.add(existing_item)

    session.flush()

    categories = list(session.exec(select(CostType).order_by(CostType.name)).all())
    existing_subcategories = list(session.exec(select(CostSubcategory)).all())
    default_subcategory_by_category: dict[int, CostSubcategory] = {}
    for category in categories:
        if category.id is None:
            continue
        expected_name = default_subcategory_name_for_cost_type(category.name)
        category_subcategories = [item for item in existing_subcategories if item.cost_type_id == category.id]
        expected_item = next(
            (item for item in category_subcategories if item.name.casefold() == expected_name.casefold()),
            None,
        )
        system_default_items = [item for item in category_subcategories if item.is_system_default]
        legacy_default = next(
            (item for item in category_subcategories if item.name.casefold() == DEFAULT_SUBCATEGORY_NAME.casefold()),
            None,
        )

        default_subcategory = expected_item or (system_default_items[0] if system_default_items else legacy_default)
        if default_subcategory is None:
            default_subcategory = CostSubcategory(
                cost_type_id=category.id,
                name=expected_name,
                is_system_default=True,
                active=True,
                archived_with_parent=False,
            )
            session.add(default_subcategory)
            session.flush()
        else:
            if default_subcategory.name != expected_name:
                default_subcategory.name = expected_name
            if not default_subcategory.is_system_default:
                default_subcategory.is_system_default = True
            if not default_subcategory.active:
                default_subcategory.active = True
            if default_subcategory.archived_with_parent:
                default_subcategory.archived_with_parent = False
            session.add(default_subcategory)

        for item in system_default_items:
            if default_subcategory.id is not None and item.id == default_subcategory.id:
                continue
            if item.is_system_default:
                item.is_system_default = False
                session.add(item)

        default_subcategory_by_category[category.id] = default_subcategory

    allocations_without_subcategory = list(
        session.exec(select(CostAllocation).where(CostAllocation.cost_subcategory_id.is_(None))).all()
    )
    for allocation in allocations_without_subcategory:
        if allocation.cost_type_id is None:
            continue
        default_subcategory = default_subcategory_by_category.get(allocation.cost_type_id)
        if default_subcategory and default_subcategory.id is not None:
            allocation.cost_subcategory_id = default_subcategory.id
            session.add(allocation)

    default_cost_area = session.exec(select(CostArea).where(CostArea.name == DEFAULT_HIDDEN_COST_AREA_NAME)).first()
    if default_cost_area and default_cost_area.id is not None:
        allocations_without_cost_area = list(
            session.exec(
                select(CostAllocation).where(
                    CostAllocation.project_id.is_(None),
                    CostAllocation.cost_area_id.is_(None),
                )
            ).all()
        )
        for allocation in allocations_without_cost_area:
            allocation.cost_area_id = default_cost_area.id
            session.add(allocation)

    _backfill_cost_allocation_statuses(session)

    session.exec(text("CREATE INDEX IF NOT EXISTS ix_cost_allocation_receipt ON cost_allocation (receipt_id)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_cost_allocation_cost_type ON cost_allocation (cost_type_id)"))
    session.exec(
        text("CREATE INDEX IF NOT EXISTS ix_cost_allocation_cost_subcategory ON cost_allocation (cost_subcategory_id)")
    )
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_cost_allocation_project ON cost_allocation (project_id)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_cost_allocation_cost_area ON cost_allocation (cost_area_id)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_cost_allocation_status ON cost_allocation (status)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_cost_area_name ON cost_area (name)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_cost_type_name ON cost_type (name)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_cost_subcategory_name ON cost_subcategory (name)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_cost_subcategory_cost_type ON cost_subcategory (cost_type_id)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_contact_category_name ON contact_category (name)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_contact_contact_category ON contact (contact_category_id)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_contact_given_name ON contact (given_name)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_contact_family_name ON contact (family_name)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_contact_organisation ON contact (organisation)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_contact_email ON contact (email)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_contact_street ON contact (street)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_contact_postal_code ON contact (postal_code)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_contact_city ON contact (city)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_contact_country ON contact (country)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_sales_order_contact_id ON sales_order (contact_id)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_sales_order_sale_date ON sales_order (sale_date)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_sales_order_invoice_date ON sales_order (invoice_date)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_sales_order_deleted_at ON sales_order (deleted_at)"))
    session.exec(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_sales_order_internal_number ON sales_order (internal_number)"))
    session.exec(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_sales_order_invoice_number ON sales_order (invoice_number)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_sales_order_item_order_id ON sales_order_item (order_id)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_sales_order_item_project_id ON sales_order_item (project_id)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_sales_order_item_position ON sales_order_item (position)"))
    session.commit()


def _migration_0001_receipt_document_type_and_notes(session: Session) -> None:
    receipt_columns = _get_table_columns(session, "receipt")
    if receipt_columns:
        _add_column_if_missing(session, "receipt", receipt_columns, "ocr_pdf_path", "TEXT")
        _add_column_if_missing(session, "receipt", receipt_columns, "thumbnail_path", "TEXT")
        _add_column_if_missing(session, "receipt", receipt_columns, "ocr_text", "TEXT")
        _add_column_if_missing(session, "receipt", receipt_columns, "doc_date", "DATE")
        _add_column_if_missing(session, "receipt", receipt_columns, "amount_gross_cents", "INTEGER")
        _add_column_if_missing(session, "receipt", receipt_columns, "vat_rate_percent", "FLOAT")
        _add_column_if_missing(session, "receipt", receipt_columns, "amount_net_cents", "INTEGER")
        _add_column_if_missing(session, "receipt", receipt_columns, "notes", "TEXT")
        _add_column_if_missing(session, "receipt", receipt_columns, "document_type", "TEXT DEFAULT 'invoice'")
        _add_column_if_missing(session, "receipt", receipt_columns, "error_message", "TEXT")
        _add_column_if_missing(session, "receipt", receipt_columns, "supplier_id", "INTEGER")
        _add_column_if_missing(session, "receipt", receipt_columns, "import_batch_id", "INTEGER")
        _add_column_if_missing(session, "receipt", receipt_columns, "created_at", "TIMESTAMP")
        _add_column_if_missing(session, "receipt", receipt_columns, "updated_at", "TIMESTAMP")
        _add_column_if_missing(session, "receipt", receipt_columns, "deleted_at", "TIMESTAMP")
        session.exec(
            text(
                "UPDATE receipt SET document_type = 'invoice' "
                "WHERE document_type IS NULL OR TRIM(document_type) = ''"
            )
        )


def _migration_0002_cost_type_active(session: Session) -> None:
    cost_type_columns = _get_table_columns(session, "cost_type")
    if cost_type_columns:
        _add_column_if_missing(session, "cost_type", cost_type_columns, "color", "TEXT DEFAULT '#ff9f1c'")
        _add_column_if_missing(session, "cost_type", cost_type_columns, "icon", "TEXT DEFAULT 'label'")
        _add_column_if_missing(session, "cost_type", cost_type_columns, "active", "BOOLEAN DEFAULT 1")
        session.exec(text("UPDATE cost_type SET active = 1 WHERE active IS NULL"))


def _migration_0003_cost_area_icon_and_project_price(session: Session) -> None:
    cost_area_columns = _get_table_columns(session, "cost_area")
    if cost_area_columns:
        _add_column_if_missing(session, "cost_area", cost_area_columns, "color", "TEXT DEFAULT '#4d96ff'")
        _add_column_if_missing(
            session,
            "cost_area",
            cost_area_columns,
            "icon",
            f"TEXT DEFAULT '{DEFAULT_COST_AREA_ICON}'",
        )
        _add_column_if_missing(session, "cost_area", cost_area_columns, "active", "BOOLEAN DEFAULT 1")

    project_columns = _get_table_columns(session, "project")
    if project_columns:
        _add_column_if_missing(session, "project", project_columns, "color", "TEXT DEFAULT '#2ec4b6'")
        _add_column_if_missing(session, "project", project_columns, "active", "BOOLEAN DEFAULT 1")
        _add_column_if_missing(session, "project", project_columns, "price_cents", "INTEGER")
        _add_column_if_missing(session, "project", project_columns, "cover_image_path", "TEXT")
        _add_column_if_missing(session, "project", project_columns, "created_on", "DATE")


def _migration_0004_cost_subcategory_and_allocation(session: Session) -> None:
    cost_subcategory_columns = _get_table_columns(session, "cost_subcategory")
    if cost_subcategory_columns:
        _add_column_if_missing(session, "cost_subcategory", cost_subcategory_columns, "active", "BOOLEAN DEFAULT 1")
        _add_column_if_missing(session, "cost_subcategory", cost_subcategory_columns, "archived_with_parent", "BOOLEAN DEFAULT 0")
        _add_column_if_missing(session, "cost_subcategory", cost_subcategory_columns, "created_at", "TIMESTAMP")
        _add_column_if_missing(session, "cost_subcategory", cost_subcategory_columns, "updated_at", "TIMESTAMP")
        session.exec(text("UPDATE cost_subcategory SET archived_with_parent = 0 WHERE archived_with_parent IS NULL"))

    cost_allocation_columns = _get_table_columns(session, "cost_allocation")
    if cost_allocation_columns:
        _add_column_if_missing(session, "cost_allocation", cost_allocation_columns, "cost_subcategory_id", "INTEGER")
        _add_column_if_missing(session, "cost_allocation", cost_allocation_columns, "created_at", "TIMESTAMP")
        _add_column_if_missing(session, "cost_allocation", cost_allocation_columns, "updated_at", "TIMESTAMP")


def _migration_0005_contact_category_icon(session: Session) -> None:
    contact_category_columns = _get_table_columns(session, "contact_category")
    if contact_category_columns and "icon" not in contact_category_columns:
        session.exec(text(f"ALTER TABLE contact_category ADD COLUMN icon TEXT DEFAULT '{DEFAULT_CONTACT_CATEGORY_ICON}'"))
    if contact_category_columns:
        if "icon" in contact_category_columns:
            session.exec(
                text(
                    f"UPDATE contact_category SET icon = '{DEFAULT_CONTACT_CATEGORY_ICON}' "
                    "WHERE icon IS NULL OR TRIM(icon) = ''"
                )
            )


def _migration_0006_contact_address_fields(session: Session) -> None:
    contact_columns = _get_table_columns(session, "contact")
    if contact_columns and "created_at" not in contact_columns:
        session.exec(text("ALTER TABLE contact ADD COLUMN created_at TIMESTAMP"))
    if contact_columns and "updated_at" not in contact_columns:
        session.exec(text("ALTER TABLE contact ADD COLUMN updated_at TIMESTAMP"))
    if contact_columns and "street" not in contact_columns:
        session.exec(text("ALTER TABLE contact ADD COLUMN street TEXT"))
    if contact_columns and "house_number" not in contact_columns:
        session.exec(text("ALTER TABLE contact ADD COLUMN house_number TEXT"))
    if contact_columns and "address_extra" not in contact_columns:
        session.exec(text("ALTER TABLE contact ADD COLUMN address_extra TEXT"))
    if contact_columns and "postal_code" not in contact_columns:
        session.exec(text("ALTER TABLE contact ADD COLUMN postal_code TEXT"))
    if contact_columns and "country" not in contact_columns:
        session.exec(text("ALTER TABLE contact ADD COLUMN country TEXT DEFAULT 'DE'"))
    if contact_columns:
        session.exec(text("UPDATE contact SET country = 'DE' WHERE country IS NULL OR TRIM(country) = ''"))


def _migration_0007_sales_order_invoice_document_fields(session: Session) -> None:
    order_columns = _get_table_columns(session, "sales_order")
    if order_columns:
        _add_column_if_missing(session, "sales_order", order_columns, "invoice_document_path", "TEXT")
        _add_column_if_missing(session, "sales_order", order_columns, "invoice_document_original_filename", "TEXT")
        _add_column_if_missing(session, "sales_order", order_columns, "invoice_document_uploaded_at", "TIMESTAMP")
        _add_column_if_missing(session, "sales_order", order_columns, "notes", "TEXT")
        _add_column_if_missing(session, "sales_order", order_columns, "created_at", "TIMESTAMP")
        _add_column_if_missing(session, "sales_order", order_columns, "updated_at", "TIMESTAMP")


def _migration_0008_supplier_and_import_batch_fields(session: Session) -> None:
    supplier_columns = _get_table_columns(session, "supplier")
    if supplier_columns:
        _add_column_if_missing(session, "supplier", supplier_columns, "active", "BOOLEAN DEFAULT 1")
        _add_column_if_missing(session, "supplier", supplier_columns, "created_at", "TIMESTAMP")
        _add_column_if_missing(session, "supplier", supplier_columns, "updated_at", "TIMESTAMP")

    import_batch_columns = _get_table_columns(session, "import_batch")
    if import_batch_columns:
        _add_column_if_missing(session, "import_batch", import_batch_columns, "started_at", "TIMESTAMP")
        _add_column_if_missing(session, "import_batch", import_batch_columns, "finished_at", "TIMESTAMP")
        _add_column_if_missing(session, "import_batch", import_batch_columns, "total_count", "INTEGER DEFAULT 0")
        _add_column_if_missing(session, "import_batch", import_batch_columns, "imported_count", "INTEGER DEFAULT 0")
        _add_column_if_missing(session, "import_batch", import_batch_columns, "error_count", "INTEGER DEFAULT 0")


def _migration_0009_invoice_profile_and_order_document_metadata(session: Session) -> None:
    session.exec(
        text(
            """
            CREATE TABLE IF NOT EXISTS invoice_profile (
                id INTEGER PRIMARY KEY,
                display_name TEXT,
                street TEXT,
                house_number TEXT,
                address_extra TEXT,
                postal_code TEXT,
                city TEXT,
                country TEXT DEFAULT 'DE',
                email TEXT,
                phone TEXT,
                website TEXT,
                tax_id_type TEXT DEFAULT 'tax_number',
                tax_id_value TEXT,
                bank_account_holder TEXT,
                iban TEXT,
                bic TEXT,
                payment_term_days INTEGER,
                logo_path TEXT,
                invoice_template_mode TEXT DEFAULT 'standard',
                created_at TIMESTAMP,
                updated_at TIMESTAMP
            )
            """
        )
    )

    profile_exists = session.exec(text("SELECT id FROM invoice_profile WHERE id = 1")).first()
    if profile_exists is None:
        session.exec(
            text(
                """
                INSERT INTO invoice_profile (
                    id, country, tax_id_type, invoice_template_mode, created_at, updated_at
                ) VALUES (
                    1, 'DE', 'tax_number', 'standard', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            )
        )

    order_columns = _get_table_columns(session, "sales_order")
    if order_columns:
        _add_column_if_missing(session, "sales_order", order_columns, "invoice_document_updated_at", "TIMESTAMP")
        _add_column_if_missing(session, "sales_order", order_columns, "invoice_document_source", "TEXT")
        session.exec(
            text(
                "UPDATE sales_order SET invoice_document_source = 'uploaded' "
                "WHERE invoice_document_path IS NOT NULL AND TRIM(invoice_document_path) <> '' "
                "AND (invoice_document_source IS NULL OR TRIM(invoice_document_source) = '')"
            )
        )
        session.exec(
            text(
                "UPDATE sales_order SET invoice_document_updated_at = invoice_document_uploaded_at "
                "WHERE invoice_document_updated_at IS NULL AND invoice_document_uploaded_at IS NOT NULL"
            )
        )


def _migration_0010_app_user_password_changed_at(session: Session) -> None:
    user_columns = _get_table_columns(session, "app_user")
    if user_columns:
        _add_column_if_missing(session, "app_user", user_columns, "password_changed_at", "TIMESTAMP")


def _migration_0011_cost_allocation_status(session: Session) -> None:
    cost_allocation_columns = _get_table_columns(session, "cost_allocation")
    if not cost_allocation_columns:
        return

    table_info = {str(row[1]).casefold(): row for row in session.exec(text("PRAGMA table_info(cost_allocation)")).all()}
    cost_type_info = table_info.get("cost_type_id")
    status_missing = "status" not in cost_allocation_columns
    cost_type_not_nullable = bool(cost_type_info and int(cost_type_info[3]))

    if not status_missing and not cost_type_not_nullable:
        return

    status_select = "status" if "status" in cost_allocation_columns else f"'{COST_ALLOCATION_STATUS_POSTED}'"
    session.exec(text("DROP TABLE IF EXISTS cost_allocation__new"))
    session.exec(
        text(
            f"""
            CREATE TABLE cost_allocation__new (
                id INTEGER PRIMARY KEY,
                receipt_id INTEGER NOT NULL,
                cost_type_id INTEGER,
                cost_subcategory_id INTEGER,
                project_id INTEGER,
                cost_area_id INTEGER,
                amount_cents INTEGER NOT NULL DEFAULT 0,
                position INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT '{COST_ALLOCATION_STATUS_POSTED}',
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
            """
        )
    )
    session.exec(
        text(
            f"""
            INSERT INTO cost_allocation__new (
                id, receipt_id, cost_type_id, cost_subcategory_id, project_id, cost_area_id,
                amount_cents, position, status, created_at, updated_at
            )
            SELECT
                id, receipt_id, cost_type_id, cost_subcategory_id, project_id, cost_area_id,
                amount_cents, position, {status_select}, created_at, updated_at
            FROM cost_allocation
            """
        )
    )
    session.exec(text("DROP TABLE cost_allocation"))
    session.exec(text("ALTER TABLE cost_allocation__new RENAME TO cost_allocation"))


def _migration_0012_project_notes(session: Session) -> None:
    project_columns = _get_table_columns(session, "project")
    if project_columns:
        _add_column_if_missing(session, "project", project_columns, "notes", "TEXT")


def _migration_0013_invoice_template_mode(session: Session) -> None:
    profile_columns = _get_table_columns(session, "invoice_profile")
    if profile_columns:
        _add_column_if_missing(
            session,
            "invoice_profile",
            profile_columns,
            "invoice_template_mode",
            "TEXT DEFAULT 'standard'",
        )
        session.exec(
            text(
                "UPDATE invoice_profile SET invoice_template_mode = 'standard' "
                "WHERE invoice_template_mode IS NULL OR TRIM(invoice_template_mode) = ''"
            )
        )


MIGRATIONS: tuple[MigrationStep, ...] = (
    MigrationStep(
        migration_id="0001_receipt_document_type_and_notes",
        description="Add receipt document type and notes columns.",
        apply=_migration_0001_receipt_document_type_and_notes,
    ),
    MigrationStep(
        migration_id="0002_cost_type_active",
        description="Add cost type active flag.",
        apply=_migration_0002_cost_type_active,
    ),
    MigrationStep(
        migration_id="0003_cost_area_icon_and_project_price",
        description="Add cost area icon and project price fields.",
        apply=_migration_0003_cost_area_icon_and_project_price,
    ),
    MigrationStep(
        migration_id="0004_cost_subcategory_and_allocation",
        description="Add cost subcategory archival fields and allocation linkage.",
        apply=_migration_0004_cost_subcategory_and_allocation,
    ),
    MigrationStep(
        migration_id="0005_contact_category_icon",
        description="Add contact category icon support.",
        apply=_migration_0005_contact_category_icon,
    ),
    MigrationStep(
        migration_id="0006_contact_address_fields",
        description="Add contact audit and address fields with default country.",
        apply=_migration_0006_contact_address_fields,
    ),
    MigrationStep(
        migration_id="0007_sales_order_invoice_document_fields",
        description="Add sales order notes and invoice document fields.",
        apply=_migration_0007_sales_order_invoice_document_fields,
    ),
    MigrationStep(
        migration_id="0008_supplier_and_import_batch_fields",
        description="Add supplier and import batch bookkeeping fields.",
        apply=_migration_0008_supplier_and_import_batch_fields,
    ),
    MigrationStep(
        migration_id="0009_invoice_profile_and_order_document_metadata",
        description="Add invoice profile table and sales order document metadata.",
        apply=_migration_0009_invoice_profile_and_order_document_metadata,
    ),
    MigrationStep(
        migration_id="0010_app_user_password_changed_at",
        description="Add password_changed_at for auth session invalidation.",
        apply=_migration_0010_app_user_password_changed_at,
    ),
    MigrationStep(
        migration_id="0011_cost_allocation_status",
        description="Add draft or posted status to cost allocations and allow nullable cost_type_id.",
        apply=_migration_0011_cost_allocation_status,
    ),
    MigrationStep(
        migration_id="0012_project_notes",
        description="Add notes field to projects.",
        apply=_migration_0012_project_notes,
    ),
    MigrationStep(
        migration_id="0013_invoice_template_mode",
        description="Add invoice template mode selection.",
        apply=_migration_0013_invoice_template_mode,
    ),
)


def _ensure_migration_table(session: Session) -> None:
    session.exec(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS {MIGRATION_TABLE} (
                migration_id TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    session.commit()


def _get_applied_migration_ids(session: Session) -> set[str]:
    return {str(row[0]) for row in session.exec(text(f"SELECT migration_id FROM {MIGRATION_TABLE}")).all()}


def _record_applied_migration(session: Session, migration: MigrationStep) -> None:
    session.exec(
        text(
            f"""
            INSERT INTO {MIGRATION_TABLE} (migration_id, description)
            VALUES (:migration_id, :description)
            """
        ),
        params={"migration_id": migration.migration_id, "description": migration.description},
    )


def _apply_migrations(session: Session) -> None:
    applied_migrations = _get_applied_migration_ids(session)
    for migration in MIGRATIONS:
        if migration.migration_id in applied_migrations:
            continue
        LOG.info("Applying database migration %s", migration.migration_id)
        try:
            migration.apply(session)
            _record_applied_migration(session, migration)
            session.commit()
        except Exception as exc:
            session.rollback()
            raise DatabaseMigrationError(
                f"Database migration failed at {migration.migration_id}: {migration.description}"
            ) from exc


def _apply_additive_migrations(session: Session) -> None:
    _ensure_migration_table(session)
    _apply_migrations(session)


def _get_table_columns(session: Session, table_name: str) -> set[str]:
    return {str(row[1]).strip().casefold() for row in session.exec(text(f"PRAGMA table_info({table_name})")).all()}


def _add_column_if_missing(session: Session, table_name: str, existing_columns: set[str], column_name: str, spec: str) -> None:
    if column_name.casefold() in existing_columns:
        return
    session.exec(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {spec}"))
    existing_columns.add(column_name.casefold())


def _column_info(session: Session, table_name: str, column_name: str) -> tuple | None:
    for row in session.exec(text(f"PRAGMA table_info({table_name})")).all():
        if str(row[1]).strip().casefold() == column_name.casefold():
            return row
    return None


def _backfill_cost_allocation_statuses(session: Session) -> None:
    completion_service = ReceiptCompletionService()
    receipts = list(session.exec(select(Receipt).order_by(Receipt.id)).all())
    for receipt in receipts:
        result = completion_service.evaluate_receipt(receipt)
        allocation_status = result.allocation_status_to_persist
        for allocation in receipt.allocations:
            allocation.status = allocation_status
            session.add(allocation)


def _validate_required_columns(session: Session, table_name: str, required_columns: tuple[str, ...]) -> None:
    existing_columns = _get_table_columns(session, table_name)
    missing_columns = [column for column in required_columns if column not in existing_columns]
    if missing_columns:
        missing = ", ".join(missing_columns)
        raise DatabaseMigrationError(
            f"Schema validation failed for table '{table_name}': missing columns {missing}"
        )


def _validate_column_nullable(session: Session, table_name: str, column_name: str) -> None:
    info = _column_info(session, table_name, column_name)
    if info is None:
        raise DatabaseMigrationError(
            f"Schema validation failed for table '{table_name}': missing column {column_name}"
        )
    if int(info[3]):
        raise DatabaseMigrationError(
            f"Schema validation failed for table '{table_name}': column {column_name} must allow NULL"
        )


def _validate_schema_state(session: Session) -> None:
    _validate_required_columns(
        session,
        "receipt",
        (
            "ocr_pdf_path",
            "thumbnail_path",
            "ocr_text",
            "doc_date",
            "amount_gross_cents",
            "vat_rate_percent",
            "amount_net_cents",
            "notes",
            "document_type",
            "error_message",
            "supplier_id",
            "import_batch_id",
            "created_at",
            "updated_at",
            "deleted_at",
        ),
    )
    _validate_required_columns(session, "cost_type", ("color", "icon", "active"))
    _validate_required_columns(session, "cost_area", ("color", "icon", "active"))
    _validate_required_columns(session, "project", ("color", "active", "price_cents", "cover_image_path", "created_on", "notes"))
    _validate_required_columns(session, "cost_subcategory", ("active", "archived_with_parent", "created_at", "updated_at"))
    _validate_required_columns(session, "cost_allocation", ("cost_subcategory_id", "status", "created_at", "updated_at"))
    _validate_column_nullable(session, "cost_allocation", "cost_type_id")
    _validate_required_columns(session, "contact_category", ("icon",))
    _validate_required_columns(
        session,
        "contact",
        ("created_at", "updated_at", "street", "house_number", "address_extra", "postal_code", "country"),
    )
    _validate_required_columns(
        session,
        "sales_order",
        (
            "notes",
            "invoice_document_path",
            "invoice_document_original_filename",
            "invoice_document_uploaded_at",
            "invoice_document_updated_at",
            "invoice_document_source",
            "created_at",
            "updated_at",
        ),
    )
    _validate_required_columns(
        session,
        "invoice_profile",
        (
            "display_name",
            "street",
            "house_number",
            "address_extra",
            "postal_code",
            "city",
            "country",
            "email",
            "phone",
            "website",
            "tax_id_type",
            "tax_id_value",
            "bank_account_holder",
            "iban",
            "bic",
            "payment_term_days",
            "logo_path",
            "invoice_template_mode",
            "created_at",
            "updated_at",
        ),
    )
    _validate_required_columns(session, "supplier", ("active", "created_at", "updated_at"))
    _validate_required_columns(session, "import_batch", ("started_at", "finished_at", "total_count", "imported_count", "error_count"))
    _validate_required_columns(session, "app_user", ("password_changed_at",))
