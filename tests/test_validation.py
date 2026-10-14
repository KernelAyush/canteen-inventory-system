"""Database-free checks of request boundaries and timezone configuration.

These do not verify stock transactions; those live in the PostgreSQL tests.
"""
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import schemas
from app.config import Settings
from app.domain import DomainError, piece_check
from app.main import create_app


@pytest.mark.parametrize("quantity", ["0", "-0.001", "1.2345", "1000000000", "NaN", "Infinity", "-Infinity", True, None])
def test_request_quantity_rejects_invalid_values(quantity):
    with pytest.raises(ValidationError):
        schemas.ConsumptionCreate(ingredient_id=1, quantity=quantity)


@pytest.mark.parametrize("quantity", ["0.001", "1", "1.234", "999999999.999"])
def test_valid_quantity_round_trips_as_exact_decimal_string(quantity):
    model = schemas.ConsumptionCreate(ingredient_id=1, quantity=quantity)
    assert isinstance(model.quantity, Decimal)
    assert Decimal(model.model_dump(mode="json")["quantity"]) == Decimal(quantity)


@pytest.mark.parametrize("name", ["", " ", "\t\n", "x" * 101])
def test_ingredient_name_cannot_be_blank_or_too_long(name):
    with pytest.raises(ValidationError):
        schemas.IngredientCreate(name=name, unit="kg")


@pytest.mark.parametrize("model,payload", [
    (schemas.IngredientCreate, {"name": "Rice\x00", "unit": "kg"}),
    (schemas.ConsumptionCreate, {"ingredient_id": 1, "quantity": "1", "note": "Lunch\x00"}),
    (schemas.WastageCreate, {"batch_id": 1, "quantity": "1", "reason": "Spilled\x00"}),
])
def test_nul_text_rejected_before_database(model, payload):
    with pytest.raises(ValidationError):
        model(**payload)


@pytest.mark.parametrize("payload", [{}, {"name": None}, {"low_stock_threshold": None}, {"unit": "grams"}, {"unknown": 1}])
def test_partial_updates_require_valid_nonnull_fields(payload):
    with pytest.raises(ValidationError):
        schemas.IngredientUpdate(**payload)


@pytest.mark.parametrize("reason", ["", "   ", "x" * 501])
def test_wastage_reason_validation(reason):
    with pytest.raises(ValidationError):
        schemas.WastageCreate(batch_id=1, quantity="1", reason=reason)


def test_zero_physical_count_is_valid():
    value = schemas.ReconciliationCreate(batch_id=1, counted_quantity="0", reason="Empty shelf")
    assert value.counted_quantity == 0


def test_piece_precision_rule():
    piece_check("piece", Decimal("3.000"))
    piece_check("kg", Decimal("0.125"))
    with pytest.raises(DomainError) as raised:
        piece_check("piece", Decimal("0.5"))
    assert raised.value.status == 422


def test_configuration_enforces_postgresql():
    with pytest.raises(ValidationError):
        Settings(database_url="sqlite:///example.db", _env_file=None)
    settings = Settings(database_url="postgresql://user:password@localhost/canteen", _env_file=None)
    assert settings.database_url.startswith("postgresql+psycopg://")


@pytest.mark.parametrize("url", [
    "postgresql+psycopg://u:p@localhost/canteen?dbname=other",
    "postgresql+psycopg://u:p@localhost/canteen?host=other",
    "postgresql+psycopg://u:p@localhost/canteen?service=other",
    "postgresql+psycopg:///canteen",
    "postgresql+psycopg://u:p@localhost/",
])
def test_configuration_rejects_ambiguous_connection_targets(url):
    with pytest.raises(ValidationError):
        Settings(database_url=url, _env_file=None)


def test_unknown_timezone_fails_configuration():
    with pytest.raises(ValidationError):
        Settings(database_url="postgresql+psycopg://u:p@localhost/canteen", canteen_timezone="Not/AZone", _env_file=None)


def test_business_date_uses_canteen_timezone_at_utc_boundary():
    fixed = datetime(2026, 10, 14, 20, 0, tzinfo=timezone.utc)

    class Clock:
        @staticmethod
        def now(tz):
            return fixed.astimezone(tz)

    settings = Settings(database_url="postgresql+psycopg://u:p@localhost/canteen", _env_file=None)
    with patch("app.config.datetime", Clock):
        assert settings.today().isoformat() == "2026-10-15"


def test_openapi_and_invalid_requests_without_database():
    # No database request is made: OpenAPI generation and field validation happen
    # before inventory execution. Port 1 is intentional, never an application DB.
    app = create_app(Settings(database_url="postgresql+psycopg://unused:unused@127.0.0.1:1/unused", _env_file=None))
    with TestClient(app) as client:
        assert client.get("/docs").status_code == 200
        spec = client.get("/openapi.json").json()
        assert "/api/consumptions" in spec["paths"]
        result = client.post("/api/consumptions", headers={"Idempotency-Key": "validation-test"}, json={"ingredient_id": 1, "quantity": "-1"})
        assert result.status_code == 422, result.text
        assert result.json()["error"]["code"] == "validation_error"
        assert result.json()["error"]["details"]
        missing_key = client.post("/api/consumptions", json={"ingredient_id": 1, "quantity": "1"})
        assert missing_key.status_code == 422
