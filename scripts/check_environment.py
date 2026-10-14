"""Read-only database preflight; never prints the configured connection URL."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings
from tests.db_guard import validate_test_database


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--test', action='store_true', help='Check isolated test database, without migrating it.')
    args = parser.parse_args()
    load_dotenv(ROOT / '.env')
    variable = 'TEST_DATABASE_URL' if args.test else 'DATABASE_URL'
    raw = os.environ.get(variable)
    if not raw:
        print(f'{variable} is missing. Copy .env.example to .env and configure it locally.')
        return 1
    try:
        app_raw = os.environ.get('DATABASE_URL')
        if not app_raw:
            raise ValueError('DATABASE_URL is required to compare the application and test targets.')
        # Use the same normal-URL validator as the server and the same test
        # guard as pytest, including rejection of all query-parameter routing.
        app_url = Settings(database_url=app_raw, _env_file=None).database_url
        if args.test:
            raw = validate_test_database(raw, app_url)
        else:
            raw = app_url
        url = make_url(raw)
        ZoneInfo(os.environ.get('CANTEEN_TIMEZONE', 'Asia/Kolkata'))
    except Exception:
        print('Configuration is invalid. Set DATABASE_URL and, for tests, TEST_DATABASE_URL with explicit TCP hosts and different database names, no URL query parameters, and a valid timezone.')
        return 1

    engine = None
    try:
        engine = create_engine(url, connect_args={'connect_timeout': 5})
        with engine.connect() as connection:
            connection.execute(text('SELECT 1'))
            print('PostgreSQL connection: OK.')
            if not args.test:
                cfg = Config(str(ROOT / 'alembic.ini'))
                scripts = ScriptDirectory.from_config(cfg)
                expected = set(scripts.get_heads())
                actual = set(MigrationContext.configure(connection).get_current_heads())
                if actual != expected:
                    print('Database migrations are pending. Run .\\.venv\\Scripts\\python.exe -m alembic upgrade head')
                    return 1
                print('Database migrations: current.')
            else:
                print('Dedicated test database name: OK. pytest will migrate and clear only this database.')
    except Exception as exc:
        print(f'Database preflight failed ({type(exc).__name__}). Check service, host/port, credentials, and database name.')
        return 1
    finally:
        if engine is not None:
            engine.dispose()
    return 0


if __name__ == '__main__':
    sys.exit(main())
