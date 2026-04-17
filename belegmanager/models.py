from datetime import date, datetime, timezone
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import Column, Numeric
from sqlmodel import Field, Relationship, SQLModel


class AppUser(SQLModel, table=True):
    __tablename__ = "app_user"

    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    password_hash: str
    active: bool = Field(default=True)
    is_admin: bool = Field(default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_login_at: Optional[datetime] = Field(default=None)
    locked_until: Optional[datetime] = Field(default=None)

    auth_attempts: List["AuthAttempt"] = Relationship(back_populates="user")


class AuthAttempt(SQLModel, table=True):
    __tablename__ = "auth_attempt"

    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True)
    user_id: Optional[int] = Field(default=None, foreign_key="app_user.id", index=True)
    successful: bool = Field(default=False, index=True)
    attempted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
    client_ip: Optional[str] = Field(default=None)
    user_agent: Optional[str] = Field(default=None)

    user: Optional[AppUser] = Relationship(back_populates="auth_attempts")


class CostType(SQLModel, table=True):
    __tablename__ = "cost_type"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    color: str = Field(default="#ff9f1c")
    icon: str = Field(default="label")
    active: bool = Field(default=True)

    subcategories: List["CostSubcategory"] = Relationship(back_populates="cost_type")
    allocations: List["CostAllocation"] = Relationship(back_populates="cost_type")


class CostArea(SQLModel, table=True):
    __tablename__ = "cost_area"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    color: str = Field(default="#4d96ff")
    icon: str = Field(default="widgets")
    active: bool = Field(default=True)

    allocations: List["CostAllocation"] = Relationship(back_populates="cost_area")


class CostSubcategory(SQLModel, table=True):
    __tablename__ = "cost_subcategory"

    id: Optional[int] = Field(default=None, primary_key=True)
    cost_type_id: int = Field(foreign_key="cost_type.id", index=True)
    name: str = Field(index=True)
    is_system_default: bool = Field(default=False)
    active: bool = Field(default=True)
    archived_with_parent: bool = Field(default=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    cost_type: CostType = Relationship(back_populates="subcategories")
    allocations: List["CostAllocation"] = Relationship(back_populates="cost_subcategory")


class Supplier(SQLModel, table=True):
    __tablename__ = "supplier"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    receipts: List["Receipt"] = Relationship(back_populates="supplier")


class ContactCategory(SQLModel, table=True):
    __tablename__ = "contact_category"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    icon: str = Field(default="badge")
    contacts: List["Contact"] = Relationship(back_populates="contact_category")


class Contact(SQLModel, table=True):
    __tablename__ = "contact"

    id: Optional[int] = Field(default=None, primary_key=True)
    given_name: Optional[str] = Field(default=None, index=True)
    family_name: Optional[str] = Field(default=None, index=True)
    organisation: Optional[str] = Field(default=None, index=True)
    email: Optional[str] = Field(default=None, index=True)
    phone: Optional[str] = Field(default=None)
    mobile: Optional[str] = Field(default=None)
    primary_link: Optional[str] = Field(default=None)
    street: Optional[str] = Field(default=None, index=True)
    house_number: Optional[str] = Field(default=None)
    address_extra: Optional[str] = Field(default=None)
    postal_code: Optional[str] = Field(default=None, index=True)
    city: Optional[str] = Field(default=None, index=True)
    country: Optional[str] = Field(default="DE", index=True)
    notes: Optional[str] = Field(default=None)
    contact_category_id: int = Field(foreign_key="contact_category.id", index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    contact_category: ContactCategory = Relationship(back_populates="contacts")
    orders: List["Order"] = Relationship(back_populates="contact")


class Project(SQLModel, table=True):
    __tablename__ = "project"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    color: str = Field(default="#2ec4b6")
    active: bool = Field(default=True)
    price_cents: Optional[int] = Field(default=None)
    cover_image_path: Optional[str] = Field(default=None)
    created_on: Optional[date] = Field(default=None, index=True)

    allocations: List["CostAllocation"] = Relationship(back_populates="project")
    order_items: List["OrderItem"] = Relationship(back_populates="project")


class ImportBatch(SQLModel, table=True):
    __tablename__ = "import_batch"

    id: Optional[int] = Field(default=None, primary_key=True)
    source_folder: str
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = Field(default=None)
    total_count: int = Field(default=0)
    imported_count: int = Field(default=0)
    error_count: int = Field(default=0)

    receipts: List["Receipt"] = Relationship(back_populates="batch")


class Receipt(SQLModel, table=True):
    __tablename__ = "receipt"

    id: Optional[int] = Field(default=None, primary_key=True)
    original_filename: str = Field(index=True)
    archive_path: str
    ocr_pdf_path: Optional[str] = Field(default=None)
    thumbnail_path: Optional[str] = Field(default=None)
    ocr_text: Optional[str] = Field(default=None)
    doc_date: Optional[date] = Field(default=None, index=True)
    amount_gross_cents: Optional[int] = Field(default=None)
    vat_rate_percent: Optional[float] = Field(default=None)
    amount_net_cents: Optional[int] = Field(default=None)
    notes: Optional[str] = Field(default=None)
    document_type: str = Field(default="invoice", index=True)
    status: str = Field(default="queued", index=True)
    error_message: Optional[str] = Field(default=None)

    supplier_id: Optional[int] = Field(default=None, foreign_key="supplier.id", index=True)
    import_batch_id: Optional[int] = Field(default=None, foreign_key="import_batch.id", index=True)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    deleted_at: Optional[datetime] = Field(default=None, index=True)

    supplier: Optional[Supplier] = Relationship(back_populates="receipts")
    batch: Optional[ImportBatch] = Relationship(back_populates="receipts")
    allocations: List["CostAllocation"] = Relationship(back_populates="receipt")


class CostAllocation(SQLModel, table=True):
    __tablename__ = "cost_allocation"

    id: Optional[int] = Field(default=None, primary_key=True)
    receipt_id: int = Field(foreign_key="receipt.id", index=True)
    cost_type_id: int = Field(foreign_key="cost_type.id", index=True)
    cost_subcategory_id: Optional[int] = Field(default=None, foreign_key="cost_subcategory.id", index=True)
    project_id: Optional[int] = Field(default=None, foreign_key="project.id", index=True)
    cost_area_id: Optional[int] = Field(default=None, foreign_key="cost_area.id", index=True)
    amount_cents: int = Field(default=0)
    position: int = Field(default=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    receipt: Receipt = Relationship(back_populates="allocations")
    cost_type: CostType = Relationship(back_populates="allocations")
    cost_subcategory: Optional[CostSubcategory] = Relationship(back_populates="allocations")
    project: Optional[Project] = Relationship(back_populates="allocations")
    cost_area: Optional[CostArea] = Relationship(back_populates="allocations")


class Order(SQLModel, table=True):
    __tablename__ = "sales_order"

    id: Optional[int] = Field(default=None, primary_key=True)
    internal_number: str = Field(index=True, unique=True)
    contact_id: int = Field(foreign_key="contact.id", index=True)
    sale_date: date = Field(index=True)
    invoice_date: Optional[date] = Field(default=None, index=True)
    invoice_number: Optional[str] = Field(default=None, index=True, unique=True)
    invoice_document_path: Optional[str] = Field(default=None)
    invoice_document_original_filename: Optional[str] = Field(default=None)
    invoice_document_uploaded_at: Optional[datetime] = Field(default=None)
    notes: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    deleted_at: Optional[datetime] = Field(default=None, index=True)

    contact: Contact = Relationship(back_populates="orders")
    items: List["OrderItem"] = Relationship(back_populates="order")


class OrderItem(SQLModel, table=True):
    __tablename__ = "sales_order_item"

    id: Optional[int] = Field(default=None, primary_key=True)
    order_id: int = Field(foreign_key="sales_order.id", index=True)
    position: int = Field(default=1, index=True)
    description: str
    quantity: Decimal = Field(sa_column=Column(Numeric(12, 3), nullable=False))
    unit_price_cents: int
    project_id: Optional[int] = Field(default=None, foreign_key="project.id", index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    order: Order = Relationship(back_populates="items")
    project: Optional[Project] = Relationship(back_populates="order_items")
