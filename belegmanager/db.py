from __future__ import annotations

from contextlib import contextmanager
import shutil

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine, select

from .config import settings
from .constants import (
    DEFAULT_COST_AREA_ICON,
    DEFAULT_COST_AREAS,
    DEFAULT_COST_TYPES,
    DEFAULT_SUBCATEGORY_NAME,
    default_subcategory_name_for_cost_type,
)
from .fts import init_fts
from .models import CostAllocation, CostArea, CostSubcategory, CostType

settings.ensure_dirs()
SCHEMA_VERSION = "v1.5"
SCHEMA_MARKER = settings.data_dir / "schema_version.txt"

engine = create_engine(
    f"sqlite:///{settings.db_path}",
    connect_args={"check_same_thread": False},
    echo=False,
)


@contextmanager
def session_scope() -> Session:
    with Session(engine) as session:
        yield session


def init_db() -> None:
    _ensure_schema_state()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        _apply_additive_migrations(session)
        init_fts(session)
        _seed_defaults(session)


def _ensure_schema_state() -> None:
    if not settings.db_path.exists():
        _write_schema_marker()
        return
    previous = _read_schema_marker()
    if previous == SCHEMA_VERSION:
        return
    _hard_reset()


def _hard_reset() -> None:
    engine.dispose()
    settings.db_path.unlink(missing_ok=True)
    if settings.archive_dir.exists():
        shutil.rmtree(settings.archive_dir, ignore_errors=True)
    settings.ensure_dirs()
    _write_schema_marker()


def _read_schema_marker() -> str | None:
    if not SCHEMA_MARKER.exists():
        return None
    try:
        return SCHEMA_MARKER.read_text(encoding="utf-8").strip() or None
    except Exception:
        return None


def _write_schema_marker() -> None:
    SCHEMA_MARKER.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_MARKER.write_text(SCHEMA_VERSION, encoding="utf-8")


def _seed_defaults(session: Session) -> None:
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
        default_subcategory = default_subcategory_by_category.get(allocation.cost_type_id)
        if default_subcategory and default_subcategory.id is not None:
            allocation.cost_subcategory_id = default_subcategory.id
            session.add(allocation)

    session.exec(text("CREATE INDEX IF NOT EXISTS ix_cost_allocation_receipt ON cost_allocation (receipt_id)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_cost_allocation_cost_type ON cost_allocation (cost_type_id)"))
    session.exec(
        text("CREATE INDEX IF NOT EXISTS ix_cost_allocation_cost_subcategory ON cost_allocation (cost_subcategory_id)")
    )
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_cost_allocation_project ON cost_allocation (project_id)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_cost_allocation_cost_area ON cost_allocation (cost_area_id)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_cost_area_name ON cost_area (name)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_cost_type_name ON cost_type (name)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_cost_subcategory_name ON cost_subcategory (name)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_cost_subcategory_cost_type ON cost_subcategory (cost_type_id)"))
    session.commit()


def _apply_additive_migrations(session: Session) -> None:
    receipt_columns = {str(row[1]).strip().casefold() for row in session.exec(text("PRAGMA table_info(receipt)")).all()}
    if "document_type" not in receipt_columns:
        session.exec(text("ALTER TABLE receipt ADD COLUMN document_type TEXT DEFAULT 'invoice'"))
        session.commit()
    session.exec(
        text(
            "UPDATE receipt SET document_type = 'invoice' "
            "WHERE document_type IS NULL OR TRIM(document_type) = ''"
        )
    )
    session.commit()
    if "notes" not in receipt_columns:
        session.exec(text("ALTER TABLE receipt ADD COLUMN notes TEXT"))
        session.commit()

    cost_type_columns = {str(row[1]).strip().casefold() for row in session.exec(text("PRAGMA table_info(cost_type)")).all()}
    if "active" not in cost_type_columns:
        session.exec(text("ALTER TABLE cost_type ADD COLUMN active BOOLEAN DEFAULT 1"))
        session.commit()
    session.exec(text("UPDATE cost_type SET active = 1 WHERE active IS NULL"))
    session.commit()

    cost_area_columns = {str(row[1]).strip().casefold() for row in session.exec(text("PRAGMA table_info(cost_area)")).all()}
    if "icon" not in cost_area_columns:
        session.exec(text(f"ALTER TABLE cost_area ADD COLUMN icon TEXT DEFAULT '{DEFAULT_COST_AREA_ICON}'"))
        session.commit()

    project_columns = {str(row[1]).strip().casefold() for row in session.exec(text("PRAGMA table_info(project)")).all()}
    if "price_cents" not in project_columns:
        session.exec(text("ALTER TABLE project ADD COLUMN price_cents INTEGER"))
        session.commit()

    cost_subcategory_columns = {
        str(row[1]).strip().casefold() for row in session.exec(text("PRAGMA table_info(cost_subcategory)")).all()
    }
    if "archived_with_parent" not in cost_subcategory_columns:
        session.exec(text("ALTER TABLE cost_subcategory ADD COLUMN archived_with_parent BOOLEAN DEFAULT 0"))
        session.commit()
    session.exec(text("UPDATE cost_subcategory SET archived_with_parent = 0 WHERE archived_with_parent IS NULL"))
    session.commit()

    cost_allocation_columns = {
        str(row[1]).strip().casefold() for row in session.exec(text("PRAGMA table_info(cost_allocation)")).all()
    }
    if "cost_subcategory_id" not in cost_allocation_columns:
        session.exec(text("ALTER TABLE cost_allocation ADD COLUMN cost_subcategory_id INTEGER"))
        session.commit()
