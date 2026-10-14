from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", hide_input_in_errors=True
    )
    database_url: str
    test_database_url: str | None = None
    canteen_timezone: str = "Asia/Kolkata"

    @field_validator("database_url")
    @classmethod
    def postgres_only(cls, value):
        url = make_url(value)
        if url.drivername not in ("postgresql", "postgresql+psycopg"):
            raise ValueError(
                "Use a PostgreSQL connection URL (postgresql+psycopg://...)."
            )
        if url.query:
            raise ValueError(
                "Database URLs must not contain query parameters; use an explicit TCP host, port and database path."
            )
        if not url.host or "/" in url.host or "\\" in url.host or not url.database:
            raise ValueError(
                "Database URLs require an explicit TCP host and dedicated database name."
            )
        return url.set(drivername="postgresql+psycopg").render_as_string(
            hide_password=False
        )

    @field_validator("canteen_timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(
                "Unknown IANA timezone; install tzdata on Windows."
            ) from exc
        return value

    def today(self):
        return datetime.now(ZoneInfo(self.canteen_timezone)).date()
