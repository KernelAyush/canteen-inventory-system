from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
    model_validator,
)

Quantity = Annotated[
    Decimal,
    Field(
        ge=0,
        le=Decimal("999999999.999"),
        max_digits=12,
        decimal_places=3,
        allow_inf_nan=False,
    ),
    PlainSerializer(lambda v: format(v, ".3f"), return_type=str, when_used="json"),
]
PositiveQuantity = Annotated[Quantity, Field(gt=0)]
Balance = Annotated[
    Decimal,
    PlainSerializer(lambda v: format(v, ".3f"), return_type=str, when_used="json"),
]
Unit = Literal["kg", "litre", "piece"]
UtcDatetime = Annotated[
    datetime,
    PlainSerializer(
        lambda v: v.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        return_type=str,
        when_used="json",
    ),
]
Kind = Literal["receipt", "consumption", "wastage", "adjustment"]
Name = Annotated[str, Field(min_length=1, max_length=100)]
Reason = Annotated[str, Field(min_length=1, max_length=500)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("*", mode="before")
    @classmethod
    def reject_null_characters(cls, value):
        if isinstance(value, str) and "\x00" in value:
            raise ValueError("Text fields cannot contain null characters.")
        return value


class IngredientCreate(Input):
    name: Name
    unit: Unit
    low_stock_threshold: Quantity = Decimal("0")


class IngredientUpdate(Input):
    name: Name | None = None
    unit: Unit | None = None
    low_stock_threshold: Quantity | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_fields_set or any(
            getattr(self, x) is None for x in self.model_fields_set
        ):
            raise ValueError("Provide at least one non-null field to update.")
        return self


class BatchCreate(Input):
    ingredient_id: int = Field(gt=0)
    reference: Name
    quantity: PositiveQuantity
    received_date: date
    expiry_date: date
    note: str = Field(default="", max_length=500)


class ConsumptionCreate(Input):
    ingredient_id: int = Field(gt=0)
    quantity: PositiveQuantity
    note: str = Field(default="", max_length=500)


class WastageCreate(Input):
    batch_id: int = Field(gt=0)
    quantity: PositiveQuantity
    reason: Reason


class ReconciliationCreate(Input):
    batch_id: int = Field(gt=0)
    counted_quantity: Quantity
    reason: Reason


class Confirm(Input):
    pass


class IngredientOut(BaseModel):
    id: int
    name: str
    unit: Unit
    low_stock_threshold: Balance
    total_stock: Balance
    usable_stock: Balance
    expired_stock: Balance
    is_low_stock: bool


class BatchOut(BaseModel):
    id: int
    ingredient_id: int
    ingredient_name: str
    unit: Unit
    reference: str
    received_date: date
    expiry_date: date
    balance: Balance
    version: int
    is_expired: bool


class Allocation(BaseModel):
    batch_id: int
    reference: str
    quantity: Balance


class OperationOut(BaseModel):
    operation_id: str
    kind: str
    quantity: Balance
    unit: Unit
    allocations: list[Allocation]
    business_date: date


class ReconciliationOut(BaseModel):
    id: int
    batch_id: int
    ingredient_name: str
    reference: str
    unit: Unit
    expected_quantity: Balance
    counted_quantity: Balance
    discrepancy: Balance
    reason: str
    status: Literal["pending", "confirmed"]
    expected_version: int
    created_at: UtcDatetime
    confirmed_at: UtcDatetime | None


class MovementOut(BaseModel):
    id: int
    operation_id: str
    ingredient_id: int
    ingredient_name: str
    batch_id: int
    reference: str
    unit: Unit
    kind: Kind
    quantity: Balance
    business_date: date
    note: str
    created_at: UtcDatetime


class IngredientPage(BaseModel):
    items: list[IngredientOut]
    total: int
    page: int
    page_size: int


class BatchPage(BaseModel):
    items: list[BatchOut]
    total: int
    page: int
    page_size: int


class ReconciliationPage(BaseModel):
    items: list[ReconciliationOut]
    total: int
    page: int
    page_size: int


class MovementPage(BaseModel):
    items: list[MovementOut]
    total: int
    page: int
    page_size: int


class WasteTotal(BaseModel):
    unit: Unit
    quantity: Balance


class ReconciliationTotal(BaseModel):
    unit: Unit
    net_adjustment: Balance
    absolute_discrepancy: Balance
    count: int


class DashboardOut(BaseModel):
    business_date: date
    timezone: str
    ingredient_count: int
    batch_count: int
    low_stock: list[IngredientOut]
    expiring_batches: list[BatchOut]
    expired_batches: list[BatchOut]
    wastage_by_unit: list[WasteTotal]
    reconciliation_by_unit: list[ReconciliationTotal]
    pending_reconciliations: int


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: list = []


class ErrorResponse(BaseModel):
    error: ErrorDetail
