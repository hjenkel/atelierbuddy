from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import delete, func, update as sa_update
from sqlmodel import Session, select

from ..constants import DEFAULT_COST_TYPE_ICON, DEFAULT_SUBCATEGORY_NAME, default_subcategory_name_for_cost_type
from ..db import engine
from ..models import Contact, ContactCategory, CostAllocation, CostSubcategory, CostType, Order, OrderItem, Project, Receipt, Supplier


class MasterDataService:
    MIN_NAME_LENGTH = 2
    MAX_NAME_LENGTH = 120
    MAX_CONTACT_FIELD_LENGTH = 255

    def __init__(self, db_engine=engine) -> None:
        self._engine = db_engine

    def _normalize_name(self, value: str, *, label: str = "Name") -> str:
        name = (value or "").strip()
        if len(name) < self.MIN_NAME_LENGTH or len(name) > self.MAX_NAME_LENGTH:
            raise ValueError(f"{label} muss zwischen {self.MIN_NAME_LENGTH} und {self.MAX_NAME_LENGTH} Zeichen lang sein")
        return name

    def _normalize_contact_name(self, value: str | None, *, label: str) -> str | None:
        name = (value or "").strip()
        if not name:
            return None
        if len(name) > self.MAX_NAME_LENGTH:
            raise ValueError(f"{label} darf maximal {self.MAX_NAME_LENGTH} Zeichen lang sein")
        return name

    def _normalize_optional_text(self, value: str | None, *, label: str) -> str | None:
        text = (value or "").strip()
        if not text:
            return None
        if len(text) > self.MAX_CONTACT_FIELD_LENGTH:
            raise ValueError(f"{label} darf maximal {self.MAX_CONTACT_FIELD_LENGTH} Zeichen lang sein")
        return text

    def _validate_contact_names(self, *, given_name: str | None, family_name: str | None) -> tuple[str | None, str | None]:
        normalized_given_name = self._normalize_contact_name(given_name, label="Vorname")
        normalized_family_name = self._normalize_contact_name(family_name, label="Nachname")
        if not normalized_given_name and not normalized_family_name:
            raise ValueError("Mindestens Vorname oder Nachname muss ausgefuellt sein")
        return normalized_given_name, normalized_family_name

    def create_or_update_supplier(self, *, name: str, active: bool) -> tuple[Supplier, bool]:
        normalized_name = self._normalize_name(name, label="Anbietername")
        with Session(self._engine) as session:
            existing = session.exec(select(Supplier).where(func.lower(Supplier.name) == normalized_name.casefold())).first()
            if existing:
                existing.active = bool(active)
                existing.updated_at = datetime.now(timezone.utc)
                session.add(existing)
                session.commit()
                session.refresh(existing)
                return existing, False

            supplier = Supplier(name=normalized_name, active=bool(active))
            session.add(supplier)
            session.commit()
            session.refresh(supplier)
            return supplier, True

    def update_supplier(self, *, supplier_id: int, name: str, active: bool) -> Supplier:
        normalized_name = self._normalize_name(name, label="Anbietername")
        with Session(self._engine) as session:
            supplier = session.get(Supplier, supplier_id)
            if not supplier:
                raise ValueError("Anbieter nicht gefunden")
            duplicate = session.exec(
                select(Supplier).where(
                    func.lower(Supplier.name) == normalized_name.casefold(),
                    Supplier.id != supplier_id,
                )
            ).first()
            if duplicate:
                raise ValueError("Anbietername existiert bereits")
            supplier.name = normalized_name
            supplier.active = bool(active)
            supplier.updated_at = datetime.now(timezone.utc)
            session.add(supplier)
            session.commit()
            session.refresh(supplier)
            return supplier

    def delete_supplier(self, *, supplier_id: int) -> None:
        with Session(self._engine) as session:
            session.exec(sa_update(Receipt).where(Receipt.supplier_id == supplier_id).values(supplier_id=None))
            supplier = session.get(Supplier, supplier_id)
            if not supplier:
                raise ValueError("Anbieter nicht gefunden")
            session.delete(supplier)
            session.commit()

    def create_contact(
        self,
        *,
        given_name: str | None,
        family_name: str | None,
        organisation: str | None,
        email: str | None,
        phone: str | None,
        mobile: str | None,
        primary_link: str | None,
        city: str | None,
        notes: str | None,
        contact_category_id: int,
    ) -> Contact:
        normalized_given_name, normalized_family_name = self._validate_contact_names(
            given_name=given_name,
            family_name=family_name,
        )
        with Session(self._engine) as session:
            category = session.get(ContactCategory, contact_category_id)
            if not category:
                raise ValueError("Kontaktkategorie nicht gefunden")
            contact = Contact(
                given_name=normalized_given_name,
                family_name=normalized_family_name,
                organisation=self._normalize_optional_text(organisation, label="Organisation"),
                email=self._normalize_optional_text(email, label="E-Mail"),
                phone=self._normalize_optional_text(phone, label="Telefon"),
                mobile=self._normalize_optional_text(mobile, label="Mobil"),
                primary_link=self._normalize_optional_text(primary_link, label="Link"),
                city=self._normalize_optional_text(city, label="Ort"),
                notes=self._normalize_optional_text(notes, label="Notiz"),
                contact_category_id=contact_category_id,
            )
            session.add(contact)
            session.commit()
            session.refresh(contact)
            return contact

    def update_contact(
        self,
        *,
        contact_id: int,
        given_name: str | None,
        family_name: str | None,
        organisation: str | None,
        email: str | None,
        phone: str | None,
        mobile: str | None,
        primary_link: str | None,
        city: str | None,
        notes: str | None,
        contact_category_id: int,
    ) -> Contact:
        normalized_given_name, normalized_family_name = self._validate_contact_names(
            given_name=given_name,
            family_name=family_name,
        )
        with Session(self._engine) as session:
            contact = session.get(Contact, contact_id)
            if not contact:
                raise ValueError("Kontakt nicht gefunden")
            category = session.get(ContactCategory, contact_category_id)
            if not category:
                raise ValueError("Kontaktkategorie nicht gefunden")
            contact.given_name = normalized_given_name
            contact.family_name = normalized_family_name
            contact.organisation = self._normalize_optional_text(organisation, label="Organisation")
            contact.email = self._normalize_optional_text(email, label="E-Mail")
            contact.phone = self._normalize_optional_text(phone, label="Telefon")
            contact.mobile = self._normalize_optional_text(mobile, label="Mobil")
            contact.primary_link = self._normalize_optional_text(primary_link, label="Link")
            contact.city = self._normalize_optional_text(city, label="Ort")
            contact.notes = self._normalize_optional_text(notes, label="Notiz")
            contact.contact_category_id = contact_category_id
            contact.updated_at = datetime.now(timezone.utc)
            session.add(contact)
            session.commit()
            session.refresh(contact)
            return contact

    def delete_contact(self, *, contact_id: int) -> None:
        with Session(self._engine) as session:
            contact = session.get(Contact, contact_id)
            if not contact:
                raise ValueError("Kontakt nicht gefunden")
            self._ensure_contact_can_be_deleted(session, contact_id)
            session.delete(contact)
            session.commit()

    def create_or_update_contact_category(self, *, name: str, icon: str) -> tuple[ContactCategory, bool]:
        normalized_name = self._normalize_name(name, label="Kontaktkategorie")
        normalized_icon = (icon or "badge").strip() or "badge"
        with Session(self._engine) as session:
            existing = session.exec(
                select(ContactCategory).where(func.lower(ContactCategory.name) == normalized_name.casefold())
            ).first()
            if existing:
                existing.icon = normalized_icon
                session.add(existing)
                session.commit()
                session.refresh(existing)
                return existing, False

            category = ContactCategory(name=normalized_name, icon=normalized_icon)
            session.add(category)
            session.commit()
            session.refresh(category)
            return category, True

    def update_contact_category(self, *, category_id: int, name: str, icon: str) -> ContactCategory:
        normalized_name = self._normalize_name(name, label="Kontaktkategorie")
        normalized_icon = (icon or "badge").strip() or "badge"
        with Session(self._engine) as session:
            category = session.get(ContactCategory, category_id)
            if not category:
                raise ValueError("Kontaktkategorie nicht gefunden")
            duplicate = session.exec(
                select(ContactCategory).where(
                    func.lower(ContactCategory.name) == normalized_name.casefold(),
                    ContactCategory.id != category_id,
                )
            ).first()
            if duplicate:
                raise ValueError("Kontaktkategorie existiert bereits")
            category.name = normalized_name
            category.icon = normalized_icon
            session.add(category)
            session.commit()
            session.refresh(category)
            return category

    def delete_contact_category(self, *, category_id: int) -> None:
        with Session(self._engine) as session:
            category = session.get(ContactCategory, category_id)
            if not category:
                raise ValueError("Kontaktkategorie nicht gefunden")
            self._ensure_contact_category_can_be_deleted(session, category_id)
            session.delete(category)
            session.commit()

    def create_or_update_project(
        self,
        *,
        name: str,
        active: bool,
        price_cents: int | None,
        created_on: date | None,
    ) -> tuple[Project, bool]:
        normalized_name = self._normalize_name(name, label="Projektname")
        with Session(self._engine) as session:
            existing = session.exec(select(Project).where(func.lower(Project.name) == normalized_name.casefold())).first()
            if existing:
                existing.active = bool(active)
                existing.price_cents = price_cents
                existing.created_on = created_on
                session.add(existing)
                session.commit()
                session.refresh(existing)
                return existing, False

            project = Project(
                name=normalized_name,
                color="#5c30ff",
                active=bool(active),
                price_cents=price_cents,
                created_on=created_on,
            )
            session.add(project)
            session.commit()
            session.refresh(project)
            return project, True

    def update_project(
        self,
        *,
        project_id: int,
        name: str,
        active: bool,
        price_cents: int | None,
        created_on: date | None,
    ) -> Project:
        normalized_name = self._normalize_name(name, label="Projektname")
        with Session(self._engine) as session:
            current = session.get(Project, project_id)
            if not current:
                raise ValueError("Projekt nicht gefunden")
            duplicate = session.exec(
                select(Project).where(
                    func.lower(Project.name) == normalized_name.casefold(),
                    Project.id != project_id,
                )
            ).first()
            if duplicate:
                raise ValueError("Projektname existiert bereits")
            current.name = normalized_name
            current.active = bool(active)
            current.price_cents = price_cents
            current.created_on = created_on
            session.add(current)
            session.commit()
            session.refresh(current)
            return current

    def set_project_cover(self, *, project_id: int, cover_path: str) -> str | None:
        with Session(self._engine) as session:
            project = session.get(Project, project_id)
            if not project:
                raise ValueError("Projekt nicht gefunden")
            old_cover = project.cover_image_path
            project.cover_image_path = cover_path
            session.add(project)
            session.commit()
            return old_cover

    def delete_project(self, *, project_id: int) -> str | None:
        with Session(self._engine) as session:
            project = session.get(Project, project_id)
            if not project:
                raise ValueError("Projekt nicht gefunden")
            if self._is_project_used(session, project_id):
                raise ValueError(
                    "Projekt ist bereits zugeordnet und kann nicht gelöscht werden. "
                    "Bitte entferne zuerst alle Zuordnungen manuell."
                )
            old_cover_path = project.cover_image_path
            session.delete(project)
            session.commit()
            return old_cover_path

    def _is_project_used(self, session: Session, project_id: int) -> bool:
        existing_allocation = session.exec(
            select(CostAllocation.id).where(CostAllocation.project_id == project_id).limit(1)
        ).first()
        if existing_allocation is not None:
            return True
        existing_order_item = session.exec(
            select(OrderItem.id).where(OrderItem.project_id == project_id).limit(1)
        ).first()
        return existing_order_item is not None

    def create_or_update_cost_type(self, *, name: str, icon: str) -> tuple[CostType, bool]:
        normalized_name = self._normalize_name(name, label="Name")
        normalized_icon = (icon or DEFAULT_COST_TYPE_ICON).strip() or DEFAULT_COST_TYPE_ICON
        with Session(self._engine) as session:
            existing = session.exec(select(CostType).where(func.lower(CostType.name) == normalized_name.casefold())).first()
            if existing:
                existing.icon = normalized_icon
                if not existing.active:
                    existing.active = True
                session.add(existing)
                if existing.id is not None:
                    for item in session.exec(
                        select(CostSubcategory).where(
                            CostSubcategory.cost_type_id == existing.id,
                            CostSubcategory.archived_with_parent.is_(True),
                        )
                    ).all():
                        item.active = True
                        item.archived_with_parent = False
                        session.add(item)
                    self._ensure_default_subcategory(session, existing.id)
                session.commit()
                session.refresh(existing)
                return existing, False

            category = CostType(name=normalized_name, icon=normalized_icon, active=True)
            session.add(category)
            session.flush()
            if category.id is not None:
                self._ensure_default_subcategory(session, category.id)
            session.commit()
            session.refresh(category)
            return category, True

    def update_cost_type(self, *, category_id: int, name: str, icon: str) -> CostType:
        normalized_name = self._normalize_name(name, label="Name")
        normalized_icon = (icon or DEFAULT_COST_TYPE_ICON).strip() or DEFAULT_COST_TYPE_ICON
        with Session(self._engine) as session:
            category = session.get(CostType, category_id)
            if not category:
                raise ValueError("Kostenkategorie nicht gefunden")
            duplicate = session.exec(
                select(CostType).where(
                    func.lower(CostType.name) == normalized_name.casefold(),
                    CostType.id != category_id,
                )
            ).first()
            if duplicate:
                raise ValueError("Name existiert bereits")
            category.name = normalized_name
            category.icon = normalized_icon
            session.add(category)
            self._ensure_default_subcategory(session, category_id)
            session.commit()
            session.refresh(category)
            return category

    def restore_cost_type(self, *, category_id: int) -> None:
        with Session(self._engine) as session:
            category = session.get(CostType, category_id)
            if not category:
                raise ValueError("Kostenkategorie nicht gefunden")
            category.active = True
            session.add(category)
            for item in session.exec(
                select(CostSubcategory).where(CostSubcategory.cost_type_id == category_id)
            ).all():
                if item.archived_with_parent:
                    item.active = True
                    item.archived_with_parent = False
                    session.add(item)
            self._ensure_default_subcategory(session, category_id)
            session.commit()

    def archive_or_delete_cost_type(self, *, category_id: int) -> str:
        with Session(self._engine) as session:
            category = session.get(CostType, category_id)
            if not category:
                raise ValueError("Kostenkategorie nicht gefunden")
            if self._is_cost_type_used(session, category_id):
                category.active = False
                session.add(category)
                for item in session.exec(
                    select(CostSubcategory).where(CostSubcategory.cost_type_id == category_id)
                ).all():
                    if item.active:
                        item.active = False
                        item.archived_with_parent = True
                        session.add(item)
                session.commit()
                return "archived"

            session.exec(delete(CostSubcategory).where(CostSubcategory.cost_type_id == category_id))
            session.delete(category)
            session.commit()
            return "deleted"

    def add_subcategory(self, *, category_id: int, name: str) -> tuple[CostSubcategory, bool]:
        normalized_name = self._normalize_name(name, label="Unterkategorie-Name")
        with Session(self._engine) as session:
            category = session.get(CostType, category_id)
            if not category:
                raise ValueError("Kostenkategorie nicht gefunden")
            if not category.active:
                raise ValueError("Unterkategorien können nur bei aktiven Kostenkategorien ergänzt werden")

            duplicate = session.exec(
                select(CostSubcategory).where(
                    CostSubcategory.cost_type_id == category_id,
                    func.lower(CostSubcategory.name) == normalized_name.casefold(),
                )
            ).first()
            if duplicate:
                if duplicate.active:
                    raise ValueError("Unterkategorie existiert bereits")
                duplicate.active = True
                duplicate.archived_with_parent = False
                session.add(duplicate)
                session.commit()
                session.refresh(duplicate)
                return duplicate, False

            item = CostSubcategory(
                cost_type_id=category_id,
                name=normalized_name,
                is_system_default=False,
                active=True,
                archived_with_parent=False,
            )
            session.add(item)
            session.commit()
            session.refresh(item)
            return item, True

    def subcategory_primary_action(self, *, subcategory_id: int) -> str:
        with Session(self._engine) as session:
            subcategory = session.get(CostSubcategory, subcategory_id)
            if not subcategory:
                raise ValueError("Unterkategorie nicht gefunden")
            if subcategory.is_system_default:
                raise ValueError("Die Standard-Unterkategorie kann nicht gelöscht oder archiviert werden")
            if self._is_subcategory_used(session, subcategory_id):
                subcategory.active = False
                subcategory.archived_with_parent = False
                session.add(subcategory)
                session.commit()
                return "archived"

            session.delete(subcategory)
            session.commit()
            return "deleted"

    def restore_subcategory(self, *, subcategory_id: int) -> None:
        with Session(self._engine) as session:
            subcategory = session.get(CostSubcategory, subcategory_id)
            if not subcategory:
                raise ValueError("Unterkategorie nicht gefunden")
            category = session.get(CostType, subcategory.cost_type_id)
            if not category or not category.active:
                raise ValueError("Unterkategorie kann nur wiederhergestellt werden, wenn die Kostenkategorie aktiv ist")
            subcategory.active = True
            subcategory.archived_with_parent = False
            session.add(subcategory)
            session.commit()

    def _ensure_default_subcategory(self, session: Session, category_id: int) -> None:
        category = session.get(CostType, category_id)
        if not category:
            return
        expected_name = default_subcategory_name_for_cost_type(category.name)
        existing_items = list(session.exec(select(CostSubcategory).where(CostSubcategory.cost_type_id == category_id)).all())
        expected_item = next((item for item in existing_items if item.name.casefold() == expected_name.casefold()), None)
        system_defaults = [item for item in existing_items if item.is_system_default]
        legacy_default = next(
            (item for item in existing_items if item.name.casefold() == DEFAULT_SUBCATEGORY_NAME.casefold()),
            None,
        )
        default_item = expected_item or (system_defaults[0] if system_defaults else legacy_default)
        if default_item:
            if default_item.name != expected_name:
                default_item.name = expected_name
            if not default_item.is_system_default:
                default_item.is_system_default = True
            if not default_item.active:
                default_item.active = True
            if default_item.archived_with_parent:
                default_item.archived_with_parent = False
            session.add(default_item)
        else:
            default_item = CostSubcategory(
                cost_type_id=category_id,
                name=expected_name,
                is_system_default=True,
                active=True,
                archived_with_parent=False,
            )
            session.add(default_item)
            session.flush()

        for item in system_defaults:
            if default_item.id is not None and item.id == default_item.id:
                continue
            if item.is_system_default:
                item.is_system_default = False
                session.add(item)

    def _is_cost_type_used(self, session: Session, category_id: int) -> bool:
        used = session.exec(select(CostAllocation.id).where(CostAllocation.cost_type_id == category_id)).first()
        return used is not None

    def _is_subcategory_used(self, session: Session, subcategory_id: int) -> bool:
        used = session.exec(select(CostAllocation.id).where(CostAllocation.cost_subcategory_id == subcategory_id)).first()
        return used is not None

    def _ensure_contact_can_be_deleted(self, session: Session, contact_id: int) -> None:
        used_order = session.exec(select(Order.id).where(Order.contact_id == contact_id).limit(1)).first()
        if used_order is not None:
            raise ValueError("Kontakt wird noch in Verkäufen verwendet und kann nicht gelöscht werden")

    def _ensure_contact_category_can_be_deleted(self, session: Session, category_id: int) -> None:
        used_contact = session.exec(select(Contact.id).where(Contact.contact_category_id == category_id)).first()
        if used_contact is not None:
            raise ValueError("Kontaktkategorie wird noch verwendet und kann nicht gelöscht werden")
