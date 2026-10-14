"""Inventory transactions. Call these functions inside a session.begin() block."""

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .models import ApiRequest, Batch, Ingredient, Reconciliation, StockMovement
from . import schemas as S


class DomainError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


def fail(status, code, message):
    raise DomainError(status, code, message)


def piece_check(unit, quantity):
    if unit == "piece" and quantity != quantity.to_integral_value():
        fail(
            422,
            "whole_pieces_required",
            "Quantities and thresholds for pieces must be whole numbers.",
        )


def get_ingredient(s: Session, ingredient_id: int, lock=False):
    q = select(Ingredient).where(Ingredient.id == ingredient_id)
    if lock:
        q = q.with_for_update()
    obj = s.scalar(q)
    if obj is None:
        fail(404, "ingredient_not_found", "Ingredient not found.")
    return obj


def get_batch(s, batch_id, lock=False):
    batch = s.get(Batch, batch_id)
    if batch is None:
        fail(404, "batch_not_found", "Batch not found.")
    ingredient = get_ingredient(s, batch.ingredient_id, lock=lock)
    # The lookup above can precede a wait on the ingredient lock. Refresh afterwards.
    if lock:
        s.refresh(batch)
    return batch, ingredient


def balance(s, batch_id):
    return s.scalar(
        select(func.coalesce(func.sum(StockMovement.quantity), 0)).where(
            StockMovement.batch_id == batch_id
        )
    )


def balance_subquery():
    return (
        select(
            StockMovement.batch_id, func.sum(StockMovement.quantity).label("balance")
        )
        .group_by(StockMovement.batch_id)
        .subquery()
    )


def ingredient_view(s, i, today):
    # One statement avoids a mixed total/usable snapshot during concurrent writes.
    from sqlalchemy import case

    total, usable = s.execute(
        select(
            func.coalesce(func.sum(StockMovement.quantity), 0),
            func.coalesce(
                func.sum(
                    case((Batch.expiry_date >= today, StockMovement.quantity), else_=0)
                ),
                0,
            ),
        )
        .join(Batch, Batch.id == StockMovement.batch_id)
        .where(Batch.ingredient_id == i.id)
    ).one()
    return S.IngredientOut(
        id=i.id,
        name=i.name,
        unit=i.unit,
        low_stock_threshold=i.low_stock_threshold,
        total_stock=total,
        usable_stock=usable,
        expired_stock=total - usable,
        is_low_stock=usable < i.low_stock_threshold,
    )


def batch_view(s, b, i, today, current_balance=None):
    return S.BatchOut(
        id=b.id,
        ingredient_id=b.ingredient_id,
        ingredient_name=i.name,
        unit=i.unit,
        reference=b.reference,
        received_date=b.received_date,
        expiry_date=b.expiry_date,
        balance=balance(s, b.id) if current_balance is None else current_balance,
        version=b.version,
        is_expired=b.expiry_date < today,
    )


def reconciliation_view(s, r):
    b, i = get_batch(s, r.batch_id)
    return S.ReconciliationOut(
        id=r.id,
        batch_id=b.id,
        ingredient_name=i.name,
        reference=b.reference,
        unit=i.unit,
        expected_quantity=r.expected_quantity,
        counted_quantity=r.counted_quantity,
        discrepancy=r.counted_quantity - r.expected_quantity,
        reason=r.reason,
        status=r.status,
        expected_version=r.expected_version,
        created_at=r.created_at,
        confirmed_at=r.confirmed_at,
    )


def movement_view(m, b, i):
    return S.MovementOut(
        id=m.id,
        operation_id=m.operation_id,
        ingredient_id=i.id,
        ingredient_name=i.name,
        batch_id=b.id,
        reference=b.reference,
        unit=i.unit,
        kind=m.kind,
        quantity=m.quantity,
        business_date=m.business_date,
        note=m.note,
        created_at=m.created_at,
    )


def record(s, b, kind, quantity, day, note, operation_id):
    s.add(
        StockMovement(
            batch_id=b.id,
            kind=kind,
            quantity=quantity,
            business_date=day,
            note=note,
            operation_id=operation_id,
        )
    )
    b.version += 1
    s.flush()


def idempotent(s, key, operation, payload, status, action):
    """Global key namespace. Successful JSON and stock writes commit together.

    PostgreSQL transaction advisory locks serialize same-key callers across processes.
    Hash collisions only serialize unrelated operations; the full key remains the PK.
    Always take this lock before any ingredient row lock to avoid lock inversion.
    """
    canonical = json.dumps(
        {"operation": operation, "body": payload.model_dump(mode="json")},
        sort_keys=True,
        separators=(",", ":"),
    )
    fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
    lock_id = int.from_bytes(
        hashlib.blake2b(key.encode(), digest_size=8).digest(), "big", signed=True
    )
    s.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id})
    existing = s.get(ApiRequest, key)
    if existing:
        if existing.fingerprint != fingerprint:
            fail(
                409,
                "idempotency_conflict",
                "This Idempotency-Key was used for a different request. Use a new key for a new operation.",
            )
        return existing.response, existing.status_code, True
    result = action().model_dump(mode="json")
    s.add(
        ApiRequest(
            key=key, fingerprint=fingerprint, status_code=status, response=result
        )
    )
    s.flush()
    return result, status, False


def create_ingredient(s, p, today):
    piece_check(p.unit, p.low_stock_threshold)
    name = " ".join(p.name.split())
    if s.scalar(
        select(Ingredient.id).where(Ingredient.normalized_name == name.casefold())
    ):
        fail(
            409, "duplicate_ingredient", "An ingredient with this name already exists."
        )
    i = Ingredient(
        name=name,
        normalized_name=name.casefold(),
        unit=p.unit,
        low_stock_threshold=p.low_stock_threshold,
    )
    s.add(i)
    s.flush()
    return ingredient_view(s, i, today)


def update_ingredient(s, id, p, today):
    i = get_ingredient(s, id, lock=True)
    new_unit = p.unit or i.unit
    threshold = (
        p.low_stock_threshold
        if p.low_stock_threshold is not None
        else i.low_stock_threshold
    )
    piece_check(new_unit, threshold)
    if new_unit != i.unit and s.scalar(
        select(Batch.id).where(Batch.ingredient_id == i.id).limit(1)
    ):
        fail(
            409,
            "unit_locked",
            "An ingredient's unit cannot change after stock has been received.",
        )
    if p.name is not None:
        name = " ".join(p.name.split())
        if s.scalar(
            select(Ingredient.id).where(
                Ingredient.normalized_name == name.casefold(), Ingredient.id != i.id
            )
        ):
            fail(
                409,
                "duplicate_ingredient",
                "An ingredient with this name already exists.",
            )
        i.name = name
        i.normalized_name = i.name.casefold()
    i.unit, i.low_stock_threshold = new_unit, threshold
    s.flush()
    return ingredient_view(s, i, today)


def receive(s, p, today):
    i = get_ingredient(s, p.ingredient_id, lock=True)
    piece_check(i.unit, p.quantity)
    if p.received_date > today:
        fail(422, "future_receipt", "Receipt date cannot be in the future.")
    if p.expiry_date < p.received_date:
        fail(422, "invalid_expiry", "Expiry date cannot precede the receipt date.")
    if s.scalar(
        select(Batch.id).where(
            Batch.ingredient_id == i.id, Batch.reference == p.reference
        )
    ):
        fail(
            409,
            "duplicate_batch",
            "This batch reference already exists for the ingredient.",
        )
    b = Batch(
        ingredient_id=i.id,
        reference=p.reference,
        received_date=p.received_date,
        expiry_date=p.expiry_date,
        version=0,
    )
    s.add(b)
    s.flush()
    record(s, b, "receipt", p.quantity, p.received_date, p.note, str(uuid4()))
    return batch_view(s, b, i, today)


def consume(s, p, today):
    i = get_ingredient(s, p.ingredient_id, lock=True)
    piece_check(i.unit, p.quantity)
    totals = balance_subquery()
    rows = s.execute(
        select(Batch, totals.c.balance)
        .join(totals, totals.c.batch_id == Batch.id)
        .where(
            Batch.ingredient_id == i.id,
            Batch.expiry_date >= today,
            totals.c.balance > 0,
        )
        .order_by(Batch.expiry_date, Batch.received_date, Batch.id)
    ).all()
    available = sum((qty for _, qty in rows), Decimal(0))
    if p.quantity > available:
        fail(
            409,
            "insufficient_stock",
            f"Only {available:.3f} {i.unit} of usable stock is available.",
        )
    remaining = p.quantity
    op = str(uuid4())
    allocations = []
    for b, qty in rows:
        if remaining == 0:
            break
        take = min(qty, remaining)
        record(s, b, "consumption", -take, today, p.note, op)
        allocations.append(
            S.Allocation(batch_id=b.id, reference=b.reference, quantity=take)
        )
        remaining -= take
    return S.OperationOut(
        operation_id=op,
        kind="consumption",
        quantity=p.quantity,
        unit=i.unit,
        allocations=allocations,
        business_date=today,
    )


def waste(s, p, today):
    b, i = get_batch(s, p.batch_id, lock=True)
    piece_check(i.unit, p.quantity)
    if p.quantity > balance(s, b.id):
        fail(409, "insufficient_stock", "Wastage exceeds this batch's remaining stock.")
    op = str(uuid4())
    record(s, b, "wastage", -p.quantity, today, p.reason, op)
    return S.OperationOut(
        operation_id=op,
        kind="wastage",
        quantity=p.quantity,
        unit=i.unit,
        allocations=[
            S.Allocation(batch_id=b.id, reference=b.reference, quantity=p.quantity)
        ],
        business_date=today,
    )


def preview_reconciliation(s, p):
    b, i = get_batch(s, p.batch_id, lock=True)
    piece_check(i.unit, p.counted_quantity)
    r = Reconciliation(
        batch_id=b.id,
        expected_quantity=balance(s, b.id),
        counted_quantity=p.counted_quantity,
        expected_version=b.version,
        reason=p.reason,
        status="pending",
    )
    s.add(r)
    s.flush()
    return reconciliation_view(s, r)


def confirm_reconciliation(s, id, today):
    r = s.get(Reconciliation, id)
    if r is None:
        fail(404, "reconciliation_not_found", "Reconciliation not found.")
    b, i = get_batch(s, r.batch_id, lock=True)
    s.refresh(r)
    if r.status == "confirmed":
        return reconciliation_view(s, r)
    if b.version != r.expected_version:
        fail(
            409,
            "stale_reconciliation",
            "Stock changed after this count was recorded. Recount and create a new reconciliation.",
        )
    change = r.counted_quantity - r.expected_quantity
    if change:
        record(s, b, "adjustment", change, today, r.reason, str(uuid4()))
    r.status = "confirmed"
    r.confirmed_at = datetime.now(timezone.utc)
    r.confirmed_date = today
    s.flush()
    return reconciliation_view(s, r)
