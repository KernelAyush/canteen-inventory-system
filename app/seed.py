"""Add fictional demonstration data once; never reset or overwrite existing stock.

Run after migrations: python -m app.seed
"""

from datetime import timedelta

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from .config import Settings
from . import domain as D, schemas as S
from .models import Batch, Ingredient


def main():
    config = Settings()
    today = config.today()
    engine = create_engine(config.database_url)
    created = 0
    with Session(engine) as s, s.begin():
        s.execute(text("SELECT pg_advisory_xact_lock(78317602892)"))
        examples = [
            (
                "Demo Rice",
                "kg",
                "5",
                [("DEMO-RICE-A", "10", 3), ("DEMO-RICE-B", "15", 15)],
            ),
            ("Demo Milk", "litre", "8", [("DEMO-MILK-A", "5", 1)]),
            ("Demo Eggs", "piece", "12", [("DEMO-EGGS-A", "30", 6)]),
            ("Demo Flour", "kg", "3", [("DEMO-FLOUR-EXPIRED", "2", -1)]),
        ]
        for name, unit, threshold, batches in examples:
            i = s.scalar(
                select(Ingredient).where(Ingredient.normalized_name == name.casefold())
            )
            if i is None:
                view = D.create_ingredient(
                    s,
                    S.IngredientCreate(
                        name=name, unit=unit, low_stock_threshold=threshold
                    ),
                    today,
                )
                i = s.get(Ingredient, view.id)
            if i.unit != unit:
                print(f"Skipped {name}: existing unit differs from sample data.")
                continue
            for reference, quantity, expiry_offset in batches:
                if s.scalar(
                    select(Batch.id).where(
                        Batch.ingredient_id == i.id, Batch.reference == reference
                    )
                ):
                    continue
                D.receive(
                    s,
                    S.BatchCreate(
                        ingredient_id=i.id,
                        reference=reference,
                        quantity=quantity,
                        received_date=today - timedelta(days=2),
                        expiry_date=today + timedelta(days=expiry_offset),
                        note="Fictional demonstration receipt",
                    ),
                    today,
                )
                created += 1
    engine.dispose()
    print(f"Sample data ready: {created} new batches. Existing records were preserved.")


if __name__ == "__main__":
    main()
