from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(slots=True)
class JobResult:
    receipt_id: int
    success: bool
    message: str = ""


@dataclass(slots=True)
class SearchFilters:
    query: str = ""
    project_ids: list[int] | None = None
    cost_type_ids: list[int] | None = None
    cost_subcategory_ids: list[int] | None = None
    cost_area_ids: list[int] | None = None
    supplier_ids: list[int] | None = None
    date_from: date | None = None
    date_to: date | None = None


@dataclass(slots=True)
class AllocationInput:
    cost_type_id: int
    cost_subcategory_id: int
    project_id: int | None
    cost_area_id: int | None
    amount_cents: int
    position: int


@dataclass(slots=True)
class OrderItemInput:
    description: str
    quantity: Decimal
    unit_price_cents: int
    project_id: int | None
    position: int


@dataclass(slots=True)
class CategoryTotalRow:
    cost_type_id: int
    cost_type_name: str
    total_cents: int


@dataclass(slots=True)
class SubcategoryTotalRow:
    cost_subcategory_id: int
    cost_subcategory_name: str
    total_cents: int


@dataclass(slots=True)
class ReportTotals:
    receipt_count: int
    overall_total_cents: int
    totals_by_cost_type: list[CategoryTotalRow]


@dataclass(slots=True)
class IncomeProjectTotalRow:
    project_id: int
    project_name: str
    total_cents: int


@dataclass(slots=True)
class IncomeOrderRow:
    order_id: int
    internal_number: str
    contact_name: str
    invoice_date: date
    total_cents: int


@dataclass(slots=True)
class IncomeReportTotals:
    order_count: int
    overall_total_cents: int
    totals_by_project: list[IncomeProjectTotalRow]
