import pytest
from tests.db_guard import validate_test_database


def test_accepts_separate_postgresql_database():
    url = "postgresql+psycopg://u:p@localhost/canteen_test"
    assert validate_test_database(url, "postgresql+psycopg://u:p@localhost/canteen") == url


@pytest.mark.parametrize("url", [
    "sqlite:///canteen_test",
    "postgresql+psycopg://u:p@localhost/canteen",
    "postgresql+psycopg://u:p@localhost/postgres",
    "postgresql+psycopg://u:p@localhost/",
    "postgresql+psycopg://u:p@localhost/canteen_test?host=elsewhere",
])
def test_rejects_unsafe_database_names_and_drivers(url):
    with pytest.raises(ValueError):
        validate_test_database(url, "postgresql+psycopg://u:p@localhost/canteen")


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]", "different-host"])
def test_cannot_bypass_guard_with_other_credentials_or_hostname(host):
    with pytest.raises(ValueError):
        validate_test_database(
            f"postgresql+psycopg://different:password@{host}/canteen_test",
            "postgresql+psycopg://original:password@localhost/canteen_test",
        )


@pytest.mark.parametrize("query", ["dbname=canteen", "service=production", "hostaddr=127.0.0.1", "host=other", "options=-csearch_path%3Dpublic"])
def test_connection_query_overrides_are_rejected(query):
    with pytest.raises(ValueError):
        validate_test_database(f"postgresql+psycopg://u:p@localhost/canteen_test?{query}", "postgresql+psycopg://u:p@localhost/canteen")


def test_application_url_override_is_also_rejected():
    with pytest.raises(ValueError):
        validate_test_database("postgresql+psycopg://u:p@localhost/canteen_test", "postgresql+psycopg://u:p@localhost/canteen?dbname=canteen_test")


def test_application_database_must_be_known():
    with pytest.raises(ValueError):
        validate_test_database("postgresql+psycopg://u:p@localhost/canteen_test", None)


def test_host_cannot_come_from_process_environment():
    with pytest.raises(ValueError):
        validate_test_database("postgresql+psycopg:///canteen_test", "postgresql+psycopg://u:p@localhost/canteen")
