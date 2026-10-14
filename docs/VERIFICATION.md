# Verification record

This record describes checks actually executed during project preparation. The application has not been run on your Windows laptop. Dependencies in `requirements.txt` are the exact direct versions installed in the verification environment, using Python 3.12.14.

## Executed checks

| Check | Result |
|---|---|
| Database-independent pytest run | **57 passed, 40 database tests skipped** because no native test server was configured |
| Sequential API/database suite on PostgreSQL 18.6 compiled to WebAssembly, via PGlite 0.5.9 | **92 passed, 0 failed**; five concurrency tests deliberately excluded |
| Frontend DOM interaction suite using jsdom, real FastAPI HTTP requests and the same PostgreSQL runtime | **11 checks passed, 56 HTTP requests, zero JavaScript errors** |
| Python compilation and static checks for undefined names/syntax | Passed |
| JavaScript syntax check | Passed |
| OpenAPI generation and offline PostgreSQL migration SQL generation | Passed |
| Migration/model metadata comparison | Table columns, types, constraints, foreign keys, defaults and indexes matched |
| Configuration failure probes and database URL override guards | Passed; rejected unsafe targets before connecting |
| YAML parsing and relative documentation links | Passed |

The 92-test run includes the 57 database-independent tests; these counts should not be added together. The complete pytest suite contains **97 tests**.

PostgreSQL checks exercised the real migration, SQL queries, database constraints and append-only trigger, exact decimal arithmetic, receipts, multi-batch FEFO consumption, expiry boundaries, rollback on rejected operations, wastage, reconciliation, stale counts, idempotent retries, duplicates, Unicode names, filters and pagination. These were not SQLite tests or mocked API results.

Frontend checks exercised ingredient creation, two receipts, FEFO allocations of 10 + 2 kg, insufficient-stock feedback and request-key reuse, wastage, reconciliation preview/confirmation, stale-count feedback, filtered history and dashboard updates. jsdom checks DOM behavior; it does not verify browser layout, native control rendering or visual accessibility.

## Checks still required on native PostgreSQL and Windows

The build environment only permits a root process and prohibits switching to an unprivileged user. Native PostgreSQL refuses root execution. A portable PostgreSQL WebAssembly runtime enabled sequential workflow verification, but its multiplexed single backend cannot establish native multi-session lock behavior.

Five concurrency tests are included for your native PostgreSQL server and CI:

1. Concurrent consumption cannot overdraw stock.
2. Identical concurrent retries create one operation.
3. Reusing one key for different concurrent payloads conflicts.
4. Concurrent reconciliation confirmations adjust stock once.
5. Consumption and wastage share the same ingredient stock lock.

Chromium could not start in the build runtime (SIGTRAP), so browser screenshots, responsive visual layout, and actual Chrome/Edge rendering remain unverified. Windows PowerShell scripts were statically reviewed but not executed on Windows. Docker Compose and the GitHub Actions workflow were provided but not executed here.

The installed FastAPI/Starlette test client emits an upstream HTTPX deprecation warning; it did not cause test failures with the pinned versions.

## Run the remaining checks

Follow the README setup, configure separate application and test database URLs, then run:

```powershell
.\.venv\Scripts\python.exe scripts\check_environment.py
.\scripts\test.ps1
.\scripts\run.ps1
```

With both databases configured, the expected pytest collection is 97 tests with no database skips. Fix any reported failures before claiming native PostgreSQL concurrency verification. Open the app in Chrome or Edge and follow [DEMO.md](DEMO.md), including the form, alert, and history interactions. Open `/docs` to inspect the API. CI verification is established only after the included workflow runs in your own repository.
