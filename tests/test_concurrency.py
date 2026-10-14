"""These tests require real PostgreSQL and independent simultaneous HTTP requests."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

from fastapi.testclient import TestClient
from tests.test_inventory_api import balance


def concurrent_posts(application, path, requests):
    barrier = Barrier(len(requests))

    def submit(request):
        body, key = request
        with TestClient(application) as independent_client:
            barrier.wait(timeout=15)
            result = independent_client.post("/api" + path, json=body, headers={"Idempotency-Key": key})
            return result.status_code, result.json()

    with ThreadPoolExecutor(max_workers=len(requests)) as pool:
        return list(pool.map(submit, requests))


def test_concurrent_consumption_cannot_overdraw(application, client, ingredient, batch):
    item = ingredient()
    batch(item["id"], "10", days=1)
    batch(item["id"], "15", days=2)
    requests = [({"ingredient_id": item["id"], "quantity": "10"}, str(uuid4())) for _ in range(6)]
    results = concurrent_posts(application, "/consumptions", requests)
    assert sorted(status for status, _ in results) == [201, 201, 409, 409, 409, 409], results
    assert balance(client, item["id"]) == Decimal("5")
    batches = client.get("/api/batches", params={"ingredient_id": item["id"]}).json()["items"]
    assert all(Decimal(lot["balance"]) >= 0 for lot in batches)


def test_concurrent_identical_retries_create_one_operation(application, client, ingredient, batch):
    item = ingredient()
    batch(item["id"], "10")
    key = str(uuid4())
    request = ({"ingredient_id": item["id"], "quantity": "3"}, key)
    results = concurrent_posts(application, "/consumptions", [request] * 6)
    assert [status for status, _ in results] == [201] * 6, results
    assert all(body == results[0][1] for _, body in results)
    assert balance(client, item["id"]) == Decimal("7")
    assert client.get("/api/movements", params={"kind": "consumption"}).json()["total"] == 1


def test_concurrent_same_key_different_payload_conflicts(application, client, ingredient, batch):
    item = ingredient()
    batch(item["id"], "10")
    key = str(uuid4())
    results = concurrent_posts(application, "/consumptions", [
        ({"ingredient_id": item["id"], "quantity": "2"}, key),
        ({"ingredient_id": item["id"], "quantity": "3"}, key),
    ])
    assert sorted(status for status, _ in results) == [201, 409], results
    successful = next(body for status, body in results if status == 201)
    assert balance(client, item["id"]) == Decimal("10") - Decimal(successful["quantity"])
    assert client.get("/api/movements", params={"kind": "consumption"}).json()["total"] == 1


def test_concurrent_confirmation_adjusts_once(application, client, ingredient, batch, mutate):
    item = ingredient()
    lot = batch(item["id"], "10")
    preview = mutate("/reconciliations", {"batch_id": lot["id"], "counted_quantity": "8", "reason": "Count"}).json()
    results = concurrent_posts(application, f"/reconciliations/{preview['id']}/confirm", [({}, str(uuid4())) for _ in range(4)])
    assert [status for status, _ in results] == [200] * 4, results
    assert all(body["status"] == "confirmed" for _, body in results)
    assert balance(client, item["id"]) == Decimal("8")
    assert client.get("/api/movements", params={"kind": "adjustment"}).json()["total"] == 1


def test_consumption_and_wastage_share_stock_lock(application, client, ingredient, batch):
    item = ingredient()
    lot = batch(item["id"], "10")
    barrier = Barrier(2)

    def send(operation):
        path, payload = operation
        with TestClient(application) as independent_client:
            barrier.wait(timeout=15)
            response = independent_client.post("/api" + path, headers={"Idempotency-Key": str(uuid4())}, json=payload)
            return response.status_code, response.json()

    operations = [
        ("/consumptions", {"ingredient_id": item["id"], "quantity": "8"}),
        ("/wastages", {"batch_id": lot["id"], "quantity": "8", "reason": "Spilled"}),
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(send, operations))
    assert sorted(status for status, _ in results) == [201, 409], results
    assert balance(client, item["id"]) == Decimal("2")
