"""Initial inventory schema and append-only movement ledger."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ingredients",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("normalized_name", sa.String(300), nullable=False, unique=True),
        sa.Column("unit", sa.String(10), nullable=False),
        sa.Column("low_stock_threshold", sa.Numeric(12, 3), nullable=False),
        sa.CheckConstraint("unit IN ('kg','litre','piece')", name="ingredient_unit"),
        sa.CheckConstraint("low_stock_threshold>=0", name="threshold_nonnegative"),
        sa.CheckConstraint(
            "unit != 'piece' OR low_stock_threshold=trunc(low_stock_threshold)",
            name="whole_piece_threshold",
        ),
    )
    op.create_table(
        "batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "ingredient_id",
            sa.Integer(),
            sa.ForeignKey("ingredients.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("reference", sa.String(100), nullable=False),
        sa.Column("received_date", sa.Date(), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint(
            "ingredient_id", "reference", name="unique_ingredient_batch"
        ),
        sa.CheckConstraint("expiry_date>=received_date", name="batch_dates"),
        sa.CheckConstraint("version>=0", name="batch_version"),
    )
    op.create_index("ix_batches_ingredient_id", "batches", ["ingredient_id"])
    op.create_index(
        "ix_batch_fefo",
        "batches",
        ["ingredient_id", "expiry_date", "received_date", "id"],
    )
    op.create_table(
        "stock_movements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("operation_id", sa.String(36), nullable=False),
        sa.Column(
            "batch_id",
            sa.Integer(),
            sa.ForeignKey("batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "kind IN ('receipt','consumption','wastage','adjustment')",
            name="movement_kind",
        ),
        sa.CheckConstraint("quantity!=0", name="movement_nonzero"),
        sa.CheckConstraint(
            "(kind='receipt' AND quantity>0) OR (kind IN ('consumption','wastage') AND quantity<0) OR kind='adjustment'",
            name="movement_direction",
        ),
    )
    for col in ("operation_id", "batch_id", "business_date"):
        op.create_index("ix_stock_movements_" + col, "stock_movements", [col])
    op.create_table(
        "reconciliations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "batch_id",
            sa.Integer(),
            sa.ForeignKey("batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("expected_quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("counted_quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("expected_version", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("confirmed_date", sa.Date()),
        sa.CheckConstraint(
            "expected_quantity>=0 AND counted_quantity>=0",
            name="reconciliation_quantities",
        ),
        sa.CheckConstraint(
            "status IN ('pending','confirmed')", name="reconciliation_status"
        ),
    )
    op.create_index("ix_reconciliations_batch_id", "reconciliations", ["batch_id"])
    op.create_table(
        "api_requests",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("response", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.execute("""CREATE FUNCTION reject_movement_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'Stock movements are append-only; record an adjustment instead'; END; $$""")
    op.execute(
        "CREATE TRIGGER stock_movements_append_only BEFORE UPDATE OR DELETE ON stock_movements FOR EACH ROW EXECUTE FUNCTION reject_movement_mutation()"
    )


def downgrade():
    op.execute("DROP TRIGGER stock_movements_append_only ON stock_movements")
    op.execute("DROP FUNCTION reject_movement_mutation()")
    for table in (
        "api_requests",
        "reconciliations",
        "stock_movements",
        "batches",
        "ingredients",
    ):
        op.drop_table(table)
