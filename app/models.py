from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Ingredient(Base):
    __tablename__ = "ingredients"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    normalized_name: Mapped[str] = mapped_column(String(300), unique=True)
    unit: Mapped[str] = mapped_column(String(10))
    low_stock_threshold: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    __table_args__ = (
        CheckConstraint("unit IN ('kg','litre','piece')", name="ingredient_unit"),
        CheckConstraint("low_stock_threshold >= 0", name="threshold_nonnegative"),
        CheckConstraint(
            "unit != 'piece' OR low_stock_threshold = trunc(low_stock_threshold)",
            name="whole_piece_threshold",
        ),
    )


class Batch(Base):
    __tablename__ = "batches"
    id: Mapped[int] = mapped_column(primary_key=True)
    ingredient_id: Mapped[int] = mapped_column(
        ForeignKey("ingredients.id", ondelete="RESTRICT"), index=True
    )
    reference: Mapped[str] = mapped_column(String(100))
    received_date: Mapped[date] = mapped_column(Date)
    expiry_date: Mapped[date] = mapped_column(Date)
    version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    __table_args__ = (
        UniqueConstraint("ingredient_id", "reference", name="unique_ingredient_batch"),
        CheckConstraint("expiry_date >= received_date", name="batch_dates"),
        CheckConstraint("version >= 0", name="batch_version"),
        Index("ix_batch_fefo", "ingredient_id", "expiry_date", "received_date", "id"),
    )


class StockMovement(Base):
    __tablename__ = "stock_movements"
    id: Mapped[int] = mapped_column(primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(36), index=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("batches.id", ondelete="RESTRICT"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    business_date: Mapped[date] = mapped_column(Date, index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    __table_args__ = (
        CheckConstraint(
            "kind IN ('receipt','consumption','wastage','adjustment')",
            name="movement_kind",
        ),
        CheckConstraint("quantity != 0", name="movement_nonzero"),
        CheckConstraint(
            "(kind='receipt' AND quantity>0) OR (kind IN ('consumption','wastage') AND quantity<0) OR kind='adjustment'",
            name="movement_direction",
        ),
    )


class Reconciliation(Base):
    __tablename__ = "reconciliations"
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("batches.id", ondelete="RESTRICT"), index=True
    )
    expected_quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    counted_quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    expected_version: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(12), default="pending", server_default="pending"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    confirmed_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    __table_args__ = (
        CheckConstraint(
            "expected_quantity>=0 AND counted_quantity>=0",
            name="reconciliation_quantities",
        ),
        CheckConstraint(
            "status IN ('pending','confirmed')", name="reconciliation_status"
        ),
    )


class ApiRequest(Base):
    __tablename__ = "api_requests"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int] = mapped_column(Integer)
    response: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
