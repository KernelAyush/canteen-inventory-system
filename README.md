# Canteen Inventory and Reconciliation System

A personal portfolio application for one canteen. Record batch receipts, allocate consumption by earliest expiry, track wastage, reconcile physical counts, and inspect stock history. The browser interface and REST API share a FastAPI server; PostgreSQL stores all operational data.

This is a local demonstration project with sample data. It has no login and is not configured for public deployment.

## Screenshots
### Stock overview
<img width="1870" height="982" alt="Screenshot 2026-10-14 233844" src="https://github.com/user-attachments/assets/3bc5f871-d995-4484-b708-31d7deacc1c6" />
### Ingredients and Batches
<img width="1876" height="977" alt="Screenshot 2026-10-14 234503" src="https://github.com/user-attachments/assets/548be58e-0ae9-47f1-8e8c-2dc3f2a5f22b" />
### Record Activity
<img width="1861" height="986" alt="Screenshot 2026-10-14 234516" src="https://github.com/user-attachments/assets/6a2699a7-9ca1-4945-826f-d2b86487ed3e" />
### Docs 
<img width="1226" height="982" alt="Screenshot 2026-10-14 233929" src="https://github.com/user-attachments/assets/a740e874-a05b-48f0-8f2b-0e0a3ec9f3d4" />

## Windows quick start

Your existing Windows, Python, PostgreSQL and VS Code setup is sufficient. Use a **PowerShell terminal in the extracted project folder**, where this README and `requirements.txt` are located. Python 3.11 or newer is required. Dependency versions and actual verification results are recorded in [requirements.txt](requirements.txt) and [docs/VERIFICATION.md](docs/VERIFICATION.md).

### 1. Check existing tools and create the environment

```powershell
py --version
py --list
Get-Service *postgres*
.\scripts\setup.ps1
```

`setup.ps1` checks Python, creates `.venv`, installs project dependencies there, and copies `.env.example` to `.env` only if the file is absent. It does not install PostgreSQL or modify databases. An existing global FastAPI installation is left alone.

If Windows blocks scripts, run the script with a policy override for that process only:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

No environment activation is required: the commands below explicitly use the project's Python.

### 2. Create two dedicated PostgreSQL databases

From PowerShell, run the provided initialization script using your existing PostgreSQL administrator account:

```powershell
psql -U postgres -h 127.0.0.1 -d postgres -W -f .\scripts\create_databases.sql
```

Use your existing administrator username if it is different. Enter its password when prompted, then choose a new password for `canteen_user` at the later prompt. Passwords are not command arguments.

If `psql` is not on PATH, use **SQL Shell (psql)** from Windows Start, connect to your existing server, and execute the file using its actual path, for example:

```sql
\i 'C:/Projects/canteen-inventory/scripts/create_databases.sql'
```

The script checks all requested names before changing anything. It creates the role `canteen_user` and two new databases: `canteen_inventory` and `canteen_inventory_test`. It aborts if any of those names already exists. It never drops or resets an existing database. In that case, inspect the existing setup or choose unused names in the script and `.env`. The test database name must end in `_test` and differ from the application database name.

### 3. Configure your local `.env`

Open `.env` in VS Code and replace `CHANGE_ME` in both URLs with the password for `canteen_user`. Match your server's port if it is not 5432.

```dotenv
DATABASE_URL=postgresql+psycopg://canteen_user:CHANGE_ME@127.0.0.1:5432/canteen_inventory
TEST_DATABASE_URL=postgresql+psycopg://canteen_user:CHANGE_ME@127.0.0.1:5432/canteen_inventory_test
CANTEEN_TIMEZONE=Asia/Kolkata
```

URL-encode password characters that have URL meaning (`@` → `%40`, `:` → `%3A`, `/` → `%2F`, `#` → `%23`, `%` → `%25`). `.env` is ignored by Git. Environment variables set in your terminal take precedence over the file; remove stale values if you edit `.env` but see the old settings.

Use plain TCP database URLs with an explicit host and database name, and no URL query parameters. Keep `DATABASE_URL` configured when running tests: the test guard compares it with `TEST_DATABASE_URL` before any migration or cleanup. Query-based connection overrides such as `?dbname=...` or `?host=...` are rejected.

### 4. Migrate, seed, and launch

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m app.seed
.\scripts\run.ps1
```

Open **http://127.0.0.1:8000**. API documentation is at **http://127.0.0.1:8000/docs** and health status at **http://127.0.0.1:8000/health**. Stop the server with **Ctrl+C**.

The seed adds four fictional ingredients (Demo Rice, Milk, Eggs, and Flour) and five batches, including low-stock and expired examples. Rerunning it skips existing sample references and preserves their original dates and stock history; it does not refresh old dates or reset quantities.

`run.ps1` checks database connectivity and migration status before starting. It does not apply migrations automatically. For another local port:

```powershell
.\scripts\run.ps1 -Port 8001
```

If scripts are restricted, the equivalent direct launch is:

```powershell
.\.venv\Scripts\python.exe scripts\check_environment.py
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

### 5. Run tests

```powershell
.\scripts\test.ps1
```

Or run `.\.venv\Scripts\python.exe -m pytest -q`. Tests validate the target database, apply migrations to it, then clear its application tables between tests. **Use only an empty, dedicated test database.** Never point the test URL at a database with data you want to retain. The tests require PostgreSQL and do not fall back to SQLite.

### VS Code

Use **Python: Select Interpreter** from the command palette and select `.venv\Scripts\python.exe`. Open terminals at the project root. `run.ps1` uses the same interpreter regardless of your global Python or editor selection.

## What to try

1. Add Rice in kilograms with a low-stock threshold of 5.
2. Receive 10 kg in batch A, expiring sooner, and 15 kg in batch B.
3. Consume 12 kg. Allocation uses all 10 kg from A and 2 kg from B.
4. Record 1 kg wasted from B. Its balance becomes 12 kg.
5. Count 11.5 kg in B, preview the −0.5 kg discrepancy, and confirm with a reason.
6. Inspect history: the original movements remain, plus an adjustment.
7. Try over-consuming and see the operation rejected without partial changes.

For a repeatable API demonstration, including a duplicate request, follow [docs/DEMO.md](docs/DEMO.md).

## Design and boundaries

- Quantities use exact decimal arithmetic, up to three decimal places; pieces must be whole numbers. JSON quantities are strings to preserve precision.
- Balances are calculated from an append-only movement ledger. Receipts and adjustments are positive when adding stock; consumption and wastage are negative.
- Expiry is evaluated in the configured canteen timezone. A batch can be consumed through its expiry date, inclusive.
- A receipt can be backdated but not future-dated. Consumption, wastage, and adjustments use the server's current business date.
- Low stock uses **usable** stock, excluding expired batches. Current-stock cards remain current snapshots even when report dates are filtered.
- Wastage and reconciliation totals are grouped by unit. Kilograms, litres, and pieces are never summed together.
- Stock-changing requests require an idempotency key. Database transactions, ingredient locks, and reconciliation versions protect against duplicate submissions and stale counts.
- This version has one canteen, fixed units, no authentication, no billing, no purchase-order workflow, and no multi-branch management.

Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the transaction model, [docs/API.md](docs/API.md) for endpoints, and [docs/VERIFICATION.md](docs/VERIFICATION.md) for the actual validation record.

## Optional Docker database

Use this only if you prefer Docker to your existing PostgreSQL service. The Python application still runs in the local virtual environment.

1. Install and start Docker Desktop if you choose this route.
2. Set `POSTGRES_PASSWORD` in `.env` to a local password.
3. Put the same password, URL-encoded, in both database URLs and change their ports to **5433**.
4. Run:

```powershell
docker compose up -d postgres
docker compose ps
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m app.seed
.\scripts\run.ps1
```

On a new volume, the container creates the application and test databases. Its port is bound to `127.0.0.1:5433`, so it can coexist with native PostgreSQL on 5432. `docker compose stop` stops it while retaining data. Changing the environment password after initialization does not change the database account password. Initialization SQL runs only when the volume is first created. Do not delete the data volume to troubleshoot a password issue.

## Troubleshooting

| Symptom | Check or fix |
|---|---|
| `py` is not recognized | Try `python --version`; the setup script supports either launcher. Select your existing Python install in VS Code. |
| `psql` is not recognized | Use Start → SQL Shell (psql), or your existing installation's `C:\Program Files\PostgreSQL\<version>\bin\psql.exe`. Missing PATH does not mean PostgreSQL is absent. |
| Scripts are disabled | Use the process-specific `powershell.exe -ExecutionPolicy Bypass -File ...` command above, or direct Python commands. |
| Connection refused | Check the PostgreSQL Windows service and configured host/port. Docker uses 5433 in this project. |
| Password authentication failed | Check the dedicated role password and URL encoding; do not use a different admin account's password. |
| Database does not exist | Create the dedicated databases from step 2 and match their exact names in `.env`. |
| Migration preflight fails | From the root folder, run `.\.venv\Scripts\python.exe -m alembic upgrade head` against this project's configured database. |
| Address already in use | Stop this project's previous server or use `run.ps1 -Port 8001`. |
| Old configuration is still used | Stop and restart the app. Check for existing `DATABASE_URL` or timezone environment variables in the terminal. |
| Seed command is repeated | The seed is designed to be rerunnable; it does not reset existing stock history. |
| API returns 409 | Read the response: possible insufficient stock, stale reconciliation, conflicting key, or duplicate name/reference. |
| Tests refuse the database | Use a dedicated name ending in `_test`, with a different name from the application database. |
| Tests fail to connect | Create the test database, confirm its credentials, and run `scripts/check_environment.py --test`. |
| `/docs` is blank offline | Swagger UI assets may need internet on first load; `/openapi.json` still exposes the API schema. |

For a full local reset, create another **new** database and change `.env` to it, then migrate and seed. Keep the old database until you deliberately decide its data is no longer needed.

## Project layout

| Path | Purpose |
|---|---|
| `app/` | Configuration, SQLAlchemy models, schemas, API routes, stock logic, and sample-data command |
| `app/static/` | Browser interface served by FastAPI |
| `alembic/` | Database migrations |
| `tests/` | PostgreSQL API and workflow tests, including concurrency |
| `scripts/` | Windows setup, launch, test, and read-only checks |
| `docs/` | Architecture, API, demo, and verification notes |
| `.github/workflows/ci.yml` | Fresh PostgreSQL test service and pytest on push/pull request |

The CI workflow uses an ephemeral database password derived from the run ID. It contains no production credentials. The GitHub workflow must run in your repository before its CI result can be claimed.
