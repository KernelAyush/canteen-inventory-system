"""Safety checks used before destructive cleanup of the dedicated test database."""
from sqlalchemy.engine import make_url


def validate_test_database(test_url: str, application_url: str | None) -> str:
    if not application_url:
        raise ValueError("DATABASE_URL must be set so the application database can be excluded.")
    test = make_url(test_url)
    if test.drivername not in ("postgresql", "postgresql+psycopg"):
        raise ValueError("Tests require PostgreSQL; SQLite cannot verify these transactions.")
    if not test.database or not test.database.endswith("_test"):
        raise ValueError("TEST_DATABASE_URL must name a dedicated database ending in _test.")
    # Host aliases and credentials must not defeat the guard. Requiring a distinct
    # database NAME is deliberately stricter than comparing connection strings.
    application = make_url(application_url)
    if not test.host or not application.host:
        raise ValueError("Use explicit TCP hostnames in test and application URLs.")
    if test.query or application.query:
        raise ValueError("Test and application URLs must not contain query overrides during tests.")
    if test.database == application.database:
        raise ValueError("The test database name must differ from DATABASE_URL.")
    return test.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)
