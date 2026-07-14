from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


Money = Decimal


class ProjectCreate(BaseModel):
    project_code: str
    project_name: str
    recipient_name: str
    financing_type: str = "ex-ante"
    lump_sum_rate: Decimal = Decimal("0.40")
    lump_sum_base_code: str = "1.1"
    public_funding_rate: Decimal = Decimal("0.95")
    total_monitoring_periods: int = Field(default=1, ge=1)


class Project(ProjectCreate):
    project_id: str = Field(default_factory=lambda: str(uuid4()))
    total_budget: Decimal = Decimal("0")
    active_budget_version_id: str | None = None
    status: str = "aktivní"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class MonitoringPeriodRange(BaseModel):
    monitoring_period: int = Field(ge=1, le=20)
    start_month: date
    end_month: date


class ProjectSchedule(BaseModel):
    project_start_date: date
    project_end_date: date
    periods: list[MonitoringPeriodRange]


class Sd2AttachmentRecord(BaseModel):
    """Metadata for an SD2 archive uploaded directly to the signed-in user's Drive."""

    file_name: str
    drive_file_id: str


class BudgetItem(BaseModel):
    code: str
    name: str
    parent_code: str | None = None
    level: int
    unit_custom: str | None = None
    unit_price: Decimal | None = None
    unit_count: Decimal | None = None
    total_amount: Decimal = Decimal("0")
    percentage: Decimal | None = None
    support_combination: str | None = None
    unit_preset: str | None = None
    unit_catalog: str | None = None
    category: Literal["direct", "lump_sum", "other", "informational", "ineligible"] = "direct"
    is_leaf: bool = True
    is_new: bool = False
    previous_amount: Decimal | None = None
    transfer_locked: bool = False
    minimum_remaining_amount: Decimal = Decimal("0")
    planned_future_spending: Decimal = Decimal("0")
    donor_priority: int = 100
    source_row_number: int


class BudgetAnalysis(BaseModel):
    token: str | None = None
    sha256: str
    file_name: str
    items: list[BudgetItem]
    total_amount: Decimal
    lump_sum_rate: Decimal | None
    lump_sum_base_code: str | None
    leaf_count: int
    summary_count: int
    warnings: list[str] = []
    errors: list[str] = []


class PaymentLine(BaseModel):
    source_table: str = "SD2"
    source_row_number: int | None = None
    source_page_number: int
    budget_item_code: str | None = None
    budget_item_name_raw: str | None = None
    accounting_period: str | None = None
    subject_label: str | None = None
    declared_amount: Decimal = Decimal("0")
    reduction_amount: Decimal = Decimal("0")
    approved_amount: Decimal = Decimal("0")
    mapping_status: str = "unmatched"
    mapped_budget_item_id: str | None = None


class PaymentRequest(BaseModel):
    payment_request_id: str = Field(default_factory=lambda: str(uuid4()))
    project_code: str
    project_name: str
    recipient_name: str
    sequence_number: int
    request_number: str
    request_version: int
    request_type: str
    state: str
    processing_state: str
    submitted_date: date | None = None
    finalized_date: date | None = None
    is_final_payment: bool
    is_advance_payment: bool
    declared_direct_costs: Decimal = Decimal("0")
    approved_direct_costs: Decimal = Decimal("0")
    declared_lump_sum: Decimal = Decimal("0")
    approved_lump_sum: Decimal = Decimal("0")
    clean_other_income: Decimal = Decimal("0")
    own_share: Decimal = Decimal("0")
    public_payment: Decimal = Decimal("0")
    approved_total: Decimal = Decimal("0")
    source_sha256: str
    source_file_name: str
    lines: list[PaymentLine] = []


class LumpSumEntry(BaseModel):
    lump_sum_entry_id: str = Field(default_factory=lambda: str(uuid4()))
    monitoring_period: str
    entry_date: date
    entry_mode: Literal["period", "cumulative"]
    entered_amount: Decimal
    note: str = ""


class CofinancingEntry(BaseModel):
    cofinancing_entry_id: str = Field(default_factory=lambda: str(uuid4()))
    entry_date: date
    amount: Money = Field(gt=0)
    note: str = Field(default="", max_length=200)


class Sd2MonthlyEntry(BaseModel):
    sd2_entry_id: str = Field(default_factory=lambda: str(uuid4()))
    monitoring_period: int = Field(ge=1, le=20)
    month: date
    budget_item_code: str
    gross_wage: Decimal = Decimal("0")
    employer_contributions: Decimal = Decimal("0")
    other_with_contributions: Decimal = Decimal("0")
    other_without_contributions: Decimal = Decimal("0")
    payment_date: date | None = None
    external_id: str = Field(default="", max_length=64)
    subject_id: str = Field(default="", max_length=10)
    last_name: str = Field(default="", max_length=255)
    first_name: str = Field(default="", max_length=255)
    employment_type: Literal["Smlouva", "DPC", "DPP", "DPPDo", "DPPNad"] | None = None
    work_time_fund: Decimal = Decimal("0")
    project_hours: Decimal = Decimal("0")
    description: str = Field(default="", max_length=2000)

    @property
    def total_amount(self) -> Decimal:
        return self.gross_wage + self.employer_contributions + self.other_with_contributions + self.other_without_contributions


class TransferCandidate(BaseModel):
    code: str
    budget: Decimal
    spent: Decimal
    planned: Decimal = Decimal("0")
    minimum_remaining: Decimal = Decimal("0")
    locked: bool = False
    donor_priority: int = 100
    unit_count: Decimal | None = None


class Transfer(BaseModel):
    source_code: str
    target_code: str
    amount: Decimal
