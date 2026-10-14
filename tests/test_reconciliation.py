from decimal import Decimal
from uuid import uuid4

import pytest
from tests.test_inventory_api import balance, error


@pytest.mark.parametrize("counted,expected_delta", [("8.500", "-1.500"), ("12", "2"), ("0", "-10")])
def test_confirm_appends_adjustment(client, ingredient, batch, mutate, counted, expected_delta):
    item = ingredient()
    lot = batch(item["id"], "10")
    preview = mutate("/reconciliations", {"batch_id": lot["id"], "counted_quantity": counted, "reason": "Physical count"})
    assert preview.status_code == 201, preview.text
    record = preview.json()
    assert Decimal(record["expected_quantity"]) == Decimal("10")
    assert Decimal(record["discrepancy"]) == Decimal(expected_delta)
    assert record["status"] == "pending"
    assert balance(client, item["id"]) == Decimal("10")
    confirmation = mutate(f"/reconciliations/{record['id']}/confirm", {})
    assert confirmation.status_code == 200, confirmation.text
    assert confirmation.json()["status"] == "confirmed"
    assert balance(client, item["id"]) == Decimal(counted)
    movements = client.get("/api/movements").json()["items"]
    assert len(movements) == 2
    assert {m["kind"]: Decimal(m["quantity"]) for m in movements} == {"receipt": Decimal("10"), "adjustment": Decimal(expected_delta)}


def test_stale_confirmation_rejected_even_after_balance_returns(client, ingredient, batch, mutate):
    item = ingredient()
    lot = batch(item["id"], "10")
    preview = mutate("/reconciliations", {"batch_id": lot["id"], "counted_quantity": "8", "reason": "Physical count"}).json()
    mutate("/wastages", {"batch_id": lot["id"], "quantity": "1", "reason": "Spilled"})
    error(mutate(f"/reconciliations/{preview['id']}/confirm", {}), 409)
    assert balance(client, item["id"]) == Decimal("9")
    # Restore the same numerical balance through a second counted adjustment.
    restore = mutate("/reconciliations", {"batch_id": lot["id"], "counted_quantity": "10", "reason": "Corrected count"}).json()
    assert mutate(f"/reconciliations/{restore['id']}/confirm", {}).status_code == 200
    error(mutate(f"/reconciliations/{preview['id']}/confirm", {}), 409)
    assert balance(client, item["id"]) == Decimal("10")


def test_repeat_confirmation_does_not_adjust_twice(client, ingredient, batch, mutate):
    item = ingredient()
    lot = batch(item["id"], "10")
    record = mutate("/reconciliations", {"batch_id": lot["id"], "counted_quantity": "9", "reason": "Count"}).json()
    key = str(uuid4())
    first = mutate(f"/reconciliations/{record['id']}/confirm", {}, key)
    second = mutate(f"/reconciliations/{record['id']}/confirm", {}, key)
    third = mutate(f"/reconciliations/{record['id']}/confirm", {})
    assert first.status_code == second.status_code == third.status_code == 200
    assert first.json() == second.json() == third.json()
    assert balance(client, item["id"]) == Decimal("9")
    assert client.get("/api/movements", params={"kind": "adjustment"}).json()["total"] == 1


def test_zero_discrepancy_confirms_without_movement(client, ingredient, batch, mutate):
    item = ingredient()
    lot = batch(item["id"], "10")
    record = mutate("/reconciliations", {"batch_id": lot["id"], "counted_quantity": "10", "reason": "Count checked"}).json()
    result = mutate(f"/reconciliations/{record['id']}/confirm", {})
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "confirmed"
    assert client.get("/api/movements").json()["total"] == 1


def test_reconciliation_preview_retry_and_validation(client, ingredient, batch, mutate):
    item = ingredient("Eggs", unit="piece")
    lot = batch(item["id"], "10")
    body = {"batch_id": lot["id"], "counted_quantity": "9", "reason": "Count"}
    key = str(uuid4())
    first = mutate("/reconciliations", body, key)
    replay = mutate("/reconciliations", body, key)
    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json()
    assert client.get("/api/reconciliations", params={"status": "pending"}).json()["total"] == 1
    error(mutate("/reconciliations", {**body, "counted_quantity": "9.5"}), 422)
    error(mutate("/reconciliations", {**body, "counted_quantity": "-1"}), 422)
    error(mutate("/reconciliations", {**body, "reason": "  "}), 422)
