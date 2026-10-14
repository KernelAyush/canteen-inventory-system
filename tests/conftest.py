"""PostgreSQL integration fixtures. Never create or reset an application database."""
import os
from pathlib import Path
from datetime import date, timedelta
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.db_guard import validate_test_database

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=False)


@pytest.fixture(scope="session")
def test_database_url():
    value = os.getenv("TEST_DATABASE_URL")
    if not value:
        pytest.skip("PostgreSQL integration not run: set dedicated TEST_DATABASE_URL ending _test.")
    try:
        return validate_test_database(value, os.getenv("DATABASE_URL"))
    except ValueError as exc:
        pytest.fail(str(exc), pytrace=False)


@pytest.fixture(scope="session")
def application(test_database_url):
    from app.config import Settings
    from app.main import create_app

    engine = create_engine(test_database_url)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", test_database_url.replace("%", "%%"))
    # Bind migration explicitly to the already-guarded test connection.
    with engine.begin() as connection:
        actual_name = connection.execute(text("SELECT current_database()")).scalar_one()
        if actual_name != make_url(test_database_url).database:
            pytest.fail("Connected database does not match the guarded test database name.")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    engine.dispose()
    instance = create_app(Settings(database_url=test_database_url, canteen_timezone="Asia/Kolkata"))
    yield instance
    instance.state.engine.dispose()


@pytest.fixture
def clean_database(application):
    # These names come only from this project's models, never from user input.
    from app.models import Base

    names = [f'"{table.name}"' for table in Base.metadata.sorted_tables]
    with application.state.engine.begin() as connection:
        if names:
            connection.execute(text("TRUNCATE TABLE " + ", ".join(names) + " RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def client(application, clean_database):
    with TestClient(application) as instance:
        yield instance


@pytest.fixture
def today(client):
    result = client.get("/api/config")
    assert result.status_code == 200, result.text
    return date.fromisoformat(result.json()["business_date"])


@pytest.fixture
def ingredient(client):
    def create(name="Rice", unit="kg", threshold="5.000"):
        result = client.post("/api/ingredients", json={
            "name": name, "unit": unit, "low_stock_threshold": threshold,
        })
        assert result.status_code == 201, result.text
        return result.json()
    return create


@pytest.fixture
def batch(client, today):
    def receive(ingredient_id, quantity="10.000", days=7, reference=None, key=None):
        expiry = today + timedelta(days=days)
        received = min(today, expiry)
        result = client.post("/api/batches", headers={"Idempotency-Key": key or str(uuid4())}, json={
            "ingredient_id": ingredient_id, "reference": reference or "LOT-" + uuid4().hex[:8],
            "quantity": quantity, "received_date": received.isoformat(),
            "expiry_date": expiry.isoformat(), "note": "Test receipt",
        })
        assert result.status_code == 201, result.text
        return result.json()
    return receive


@pytest.fixture
def mutate(client):
    def post(path, body, key=None):
        return client.post("/api" + path, json=body, headers={"Idempotency-Key": key or str(uuid4())})
    return post
