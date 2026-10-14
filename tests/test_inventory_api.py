from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest


def error(response, status):
    assert response.status_code == status, response.text
    body = response.json()["error"]
    assert body["code"] and body["message"]
    assert isinstance(body["details"], list)


def balance(client, ingredient_id):
    response = client.get(f"/api/ingredients/{ingredient_id}")
    assert response.status_code == 200
    return Decimal(response.json()["total_stock"])


def test_health_and_openapi(client):
    assert client.get("/health").status_code == 200
    assert client.get("/docs").status_code == 200
    document = client.get("/openapi.json").json()
    assert "/api/consumptions" in document["paths"]
    assert "/api/reconciliations/{reconciliation_id}/confirm" in document["paths"] or any(
        p.startswith("/api/reconciliations/{") and p.endswith("/confirm") for p in document["paths"]
    )


def test_receipts_persist_and_balances_are_exact(client, ingredient, batch, application):
    from fastapi.testclient import TestClient
    item = ingredient()
    batch(item["id"], "0.100")
    batch(item["id"], "0.200")
    assert balance(client, item["id"]) == Decimal("0.300")
    # Separate HTTP client/session observes the committed database state.
    with TestClient(application) as reopened:
        assert balance(reopened, item["id"]) == Decimal("0.300")


def test_ingredient_duplicate_name_and_edit(client, ingredient, batch):
    item = ingredient("Rice")
    error(client.post("/api/ingredients", json={
        "name": " rice ", "unit": "kg", "low_stock_threshold": "1",
    }), 409)
    result = client.patch(f"/api/ingredients/{item['id']}", json={"name": "Basmati Rice", "low_stock_threshold": "2.500"})
    assert result.status_code == 200, result.text
    assert result.json()["name"] == "Basmati Rice"
    batch(item["id"])
    error(client.patch(f"/api/ingredients/{item['id']}", json={"unit": "litre"}), 409)


def test_unit_can_change_before_first_movement(client, ingredient):
    item = ingredient("Oil", unit="kg")
    result = client.patch(f"/api/ingredients/{item['id']}", json={"unit": "litre"})
    assert result.status_code == 200, result.text
    assert result.json()["unit"] == "litre"


def test_unicode_casefold_expansion_does_not_overflow_name_column(client, ingredient):
    item = ingredient("ß" * 100)
    assert item["name"] == "ß" * 100
    error(client.post("/api/ingredients", json={"name": "ẞ" * 100, "unit": "kg", "low_stock_threshold": "1"}), 409)


def test_fefo_splits_quantity_and_preserves_signed_history(client, ingredient, batch, mutate):
    item = ingredient()
    later = batch(item["id"], "15", days=14, reference="LATER")
    earlier = batch(item["id"], "10", days=7, reference="EARLIER")
    response = mutate("/consumptions", {"ingredient_id": item["id"], "quantity": "12"})
    assert response.status_code == 201, response.text
    allocations = response.json()["allocations"]
    assert [(a["batch_id"], Decimal(a["quantity"])) for a in allocations] == [
        (earlier["id"], Decimal("10")), (later["id"], Decimal("2")),
    ]
    assert balance(client, item["id"]) == Decimal("13")
    history = client.get("/api/movements", params={"kind": "consumption"}).json()
    assert history["total"] == 2
    assert sum(Decimal(m["quantity"]) for m in history["items"]) == Decimal("-12")


def test_fefo_tie_break_is_deterministic(ingredient, batch, mutate):
    item = ingredient()
    first = batch(item["id"], "2", days=5)
    second = batch(item["id"], "2", days=5)
    response = mutate("/consumptions", {"ingredient_id": item["id"], "quantity": "3"})
    assert response.status_code == 201, response.text
    assert [a["batch_id"] for a in response.json()["allocations"]] == [first["id"], second["id"]]


def test_expiry_today_is_eligible_yesterday_is_not(client, ingredient, batch, mutate):
    item = ingredient(threshold="1")
    expired = batch(item["id"], "9", days=-1)
    eligible = batch(item["id"], "2", days=0)
    response = mutate("/consumptions", {"ingredient_id": item["id"], "quantity": "2"})
    assert response.status_code == 201, response.text
    assert [a["batch_id"] for a in response.json()["allocations"]] == [eligible["id"]]
    error(mutate("/consumptions", {"ingredient_id": item["id"], "quantity": "1"}), 409)
    detail = client.get(f"/api/ingredients/{item['id']}").json()
    assert Decimal(detail["total_stock"]) == Decimal("9")
    assert Decimal(detail["usable_stock"]) == 0
    assert Decimal(detail["expired_stock"]) == Decimal("9")
    assert detail["is_low_stock"] is True
    assert client.get(f"/api/batches/{expired['id']}").json()["is_expired"] is True


def test_insufficient_stock_does_not_save_partial_allocations(client, ingredient, batch, mutate):
    item = ingredient()
    batch(item["id"], "2")
    batch(item["id"], "3")
    before = client.get("/api/movements").json()["total"]
    error(mutate("/consumptions", {"ingredient_id": item["id"], "quantity": "6"}), 409)
    assert balance(client, item["id"]) == Decimal("5")
    assert client.get("/api/movements").json()["total"] == before


@pytest.mark.parametrize("quantity", ["0", "-1", "1.2345", "1000000000", "NaN", "Infinity"])
def test_invalid_quantities_do_not_change_stock(client, ingredient, batch, mutate, quantity):
    item = ingredient()
    batch(item["id"], "10")
    error(mutate("/consumptions", {"ingredient_id": item["id"], "quantity": quantity}), 422)
    assert balance(client, item["id"]) == Decimal("10")


def test_piece_quantities_and_thresholds_must_be_integral(client, ingredient, batch, mutate):
    item = ingredient("Eggs", unit="piece", threshold="5")
    batch(item["id"], "10")
    error(mutate("/consumptions", {"ingredient_id": item["id"], "quantity": "0.5"}), 422)
    error(client.patch(f"/api/ingredients/{item['id']}", json={"low_stock_threshold": "0.5"}), 422)


def test_unknown_records_have_consistent_errors(client, mutate):
    error(client.get("/api/ingredients/999999"), 404)
    error(client.get("/api/batches/999999"), 404)
    error(mutate("/consumptions", {"ingredient_id": 999999, "quantity": "1"}), 404)
    error(mutate("/wastages", {"batch_id": 999999, "quantity": "1", "reason": "Spilled"}), 404)


def test_invalid_receipt_dates_and_duplicate_reference(client, ingredient, batch, today, mutate):
    item = ingredient()
    body = {"ingredient_id": item["id"], "reference": "DATE", "quantity": "1",
            "received_date": (today + timedelta(days=1)).isoformat(),
            "expiry_date": (today + timedelta(days=4)).isoformat()}
    error(mutate("/batches", body), 422)
    body.update(received_date=today.isoformat(), expiry_date=(today - timedelta(days=1)).isoformat())
    error(mutate("/batches", body), 422)
    batch(item["id"], reference="DUPLICATE")
    body.update(reference="DUPLICATE", expiry_date=(today + timedelta(days=7)).isoformat())
    error(mutate("/batches", body), 409)


def test_wastage_requires_reason_and_handles_expired_stock(client, ingredient, batch, mutate):
    item = ingredient()
    lot = batch(item["id"], "3", days=-1)
    for reason in ["", "   "]:
        error(mutate("/wastages", {"batch_id": lot["id"], "quantity": "1", "reason": reason}), 422)
    error(mutate("/wastages", {"batch_id": lot["id"], "quantity": "4", "reason": "Expired"}), 409)
    result = mutate("/wastages", {"batch_id": lot["id"], "quantity": "2", "reason": "Expired"})
    assert result.status_code == 201, result.text
    assert balance(client, item["id"]) == Decimal("1")


def test_receipt_replay_returns_original_response(client, ingredient, today, mutate):
    item = ingredient()
    body = {"ingredient_id": item["id"], "reference": "IDEMPOTENT", "quantity": "10",
            "received_date": today.isoformat(), "expiry_date": (today + timedelta(days=1)).isoformat()}
    key = str(uuid4())
    first = mutate("/batches", body, key)
    second = mutate("/batches", body, key)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert balance(client, item["id"]) == Decimal("10")
    assert client.get("/api/movements").json()["total"] == 1
    error(mutate("/batches", {**body, "quantity": "11"}, key), 409)


def test_consumption_replay_and_conflict(client, ingredient, batch, mutate):
    item = ingredient()
    batch(item["id"], "10")
    body = {"ingredient_id": item["id"], "quantity": "3"}
    key = str(uuid4())
    first = mutate("/consumptions", body, key)
    replay = mutate("/consumptions", body, key)
    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json()
    assert balance(client, item["id"]) == Decimal("7")
    error(mutate("/consumptions", {**body, "quantity": "4"}, key), 409)


def test_equivalent_decimal_payloads_are_same_idempotent_request(client, ingredient, batch, mutate):
    item = ingredient()
    batch(item["id"], "10")
    key = str(uuid4())
    first = mutate("/consumptions", {"ingredient_id": item["id"], "quantity": "1"}, key)
    replay = mutate("/consumptions", {"ingredient_id": item["id"], "quantity": "1.000"}, key)
    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json()
    assert balance(client, item["id"]) == Decimal("9")


def test_movement_update_and_delete_are_rejected_by_database(application, client, ingredient, batch):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    item = ingredient()
    batch(item["id"], "10")
    for statement in ["UPDATE stock_movements SET quantity = 1", "DELETE FROM stock_movements"]:
        with pytest.raises(DBAPIError):
            with application.state.engine.begin() as connection:
                connection.execute(text(statement))
        assert balance(client, item["id"]) == Decimal("10")


def test_idempotency_key_required(client, ingredient):
    item = ingredient()
    error(client.post("/api/consumptions", json={"ingredient_id": item["id"], "quantity": "1"}), 422)


def test_failed_request_can_retry_same_key_after_replenishment(client, ingredient, batch, mutate):
    item = ingredient()
    key = str(uuid4())
    body = {"ingredient_id": item["id"], "quantity": "2"}
    error(mutate("/consumptions", body, key), 409)
    batch(item["id"], "3")
    result = mutate("/consumptions", body, key)
    assert result.status_code == 201, result.text
    assert balance(client, item["id"]) == Decimal("1")


def test_failure_after_first_allocation_rolls_back_entire_transaction(client, ingredient, batch, mutate, monkeypatch):
    from app import domain
    item = ingredient()
    first_batch = batch(item["id"], "2", days=1)
    second_batch = batch(item["id"], "3", days=2)
    original = domain.record

    def fail_second_allocation(session, lot, kind, quantity, day, note, operation_id):
        if lot.id == second_batch["id"] and kind == "consumption":
            raise domain.DomainError(409, "test_failure", "Simulated second-allocation failure")
        return original(session, lot, kind, quantity, day, note, operation_id)

    key = str(uuid4())
    body = {"ingredient_id": item["id"], "quantity": "4"}
    with monkeypatch.context() as patcher:
        patcher.setattr(domain, "record", fail_second_allocation)
        error(mutate("/consumptions", body, key), 409)
    assert balance(client, item["id"]) == Decimal("5")
    first_after = client.get(f"/api/batches/{first_batch['id']}").json()
    assert first_after["version"] == first_batch["version"]
    assert client.get("/api/movements", params={"kind": "consumption"}).json()["total"] == 0
    assert mutate("/consumptions", body, key).status_code == 201
    assert balance(client, item["id"]) == Decimal("1")


def test_movement_filters_and_pagination(client, ingredient, batch, mutate, today):
    rice = ingredient("Rice")
    oil = ingredient("Oil", unit="litre")
    rice_batch = batch(rice["id"], "10")
    batch(oil["id"], "3")
    assert mutate("/consumptions", {"ingredient_id": rice["id"], "quantity": "2"}).status_code == 201
    assert mutate("/wastages", {"batch_id": rice_batch["id"], "quantity": "1", "reason": "Spilled"}).status_code == 201
    page1 = client.get("/api/movements", params={"ingredient_id": rice["id"], "page_size": 1}).json()
    page2 = client.get("/api/movements", params={"ingredient_id": rice["id"], "page_size": 1, "page": 2}).json()
    assert page1["total"] == 3
    assert len(page1["items"]) == len(page2["items"]) == 1
    assert page1["items"][0]["id"] != page2["items"][0]["id"]
    assert all(m["ingredient_id"] == rice["id"] for m in page1["items"] + page2["items"])
    only_waste = client.get("/api/movements", params={"kind": "wastage", "start_date": today.isoformat(), "end_date": today.isoformat()}).json()
    assert only_waste["total"] == 1
    assert only_waste["items"][0]["batch_id"] == rice_batch["id"]
    tomorrow = (today + timedelta(days=1)).isoformat()
    assert client.get("/api/movements", params={"start_date": tomorrow}).json()["total"] == 0
    error(client.get("/api/movements", params={"page": 0}), 422)
    error(client.get("/api/movements", params={"page_size": 101}), 422)
    error(client.get("/api/movements", params={"start_date": tomorrow, "end_date": today.isoformat()}), 422)


def test_dashboard_does_not_combine_units(client, ingredient, batch, mutate, today):
    rice = ingredient("Rice", threshold="20")
    oil = ingredient("Oil", unit="litre", threshold="1")
    a = batch(rice["id"], "10", days=1)
    b = batch(oil["id"], "10", days=-1)
    mutate("/wastages", {"batch_id": a["id"], "quantity": "2", "reason": "Spilled"})
    mutate("/wastages", {"batch_id": b["id"], "quantity": "1", "reason": "Expired"})
    result = client.get("/api/dashboard").json()
    totals = {row["unit"]: Decimal(row["quantity"]) for row in result["wastage_by_unit"]}
    assert totals["kg"] == Decimal("2")
    assert totals["litre"] == Decimal("1")
    assert {b["id"] for b in result["expired_batches"]} == {b["id"]}
    assert a["id"] in {row["id"] for row in result["expiring_batches"]}
    filtered = client.get("/api/dashboard", params={"ingredient_id": rice["id"]}).json()
    assert all(row["unit"] == "kg" for row in filtered["wastage_by_unit"])
    historical = client.get("/api/dashboard", params={"end_date": (today - timedelta(days=1)).isoformat()}).json()
    assert sum(Decimal(row["quantity"]) for row in historical["wastage_by_unit"]) == 0
    assert historical["low_stock"]  # Current stock alerts are not historical totals.
