# Architecture

The browser loads HTML, CSS, and JavaScript from FastAPI and calls relative `/api/...` URLs. No separate frontend development server or CORS configuration is needed. All stock data comes from PostgreSQL through the backend.

## Data model

| Entity | Responsibility |
|---|---|
| Ingredient | Unique normalized name, fixed unit, low-stock threshold |
| Batch | Ingredient link, reference, receipt/expiry dates, stock version |
| Stock movement | Signed quantity, batch, operation identifier, business date, reason/note |
| Reconciliation | Counted/expected quantities, expected batch version, reason, confirmation state |
| API request | Successful request fingerprint and JSON response for retry protection |

One ingredient has many batches. Each batch has many movements. One consumption operation can create several movements as it allocates across batches. The balance is the sum of a batch's signed movements; there is no separately editable current-stock field.

Database migrations establish primary keys, foreign keys, uniqueness, and check constraints. A PostgreSQL trigger rejects direct UPDATE and DELETE statements on stock movements. It protects historical rows from routine edits; it does not prevent a database owner from changing the schema or truncating tables. Pydantic validates incoming field types and ranges. Service logic validates rules that depend on stored data, such as sufficient stock and integral piece quantities.

## Quantity and date rules

Numeric quantities allow three decimal places and are calculated with Python `Decimal` and PostgreSQL `NUMERIC`, never binary floats. A request quantity is at most `999999999.999`. A piece quantity must be an integer. JSON encodes decimals as strings; the frontend uses the returned strings for display.

Dates are ISO `YYYY-MM-DD`. The timezone defaults to `Asia/Kolkata`; the server's current business date determines expiry and new consumption/wastage/adjustment dates. Receipt dates may be historical, including batches already expired on arrival, but cannot be future dates. Expiry must be on or after receipt. Expired stock remains physically accounted for until wasted or reconciled; it cannot be consumed.

## Transaction and lock order

Every stock operation is one database transaction. The lock order is:

1. Take a transaction-scoped PostgreSQL advisory lock derived from the idempotency key.
2. Check whether that key already has a successful stored result.
3. Lock the ingredient row with `SELECT ... FOR UPDATE`.
4. Recalculate relevant balances, validate, allocate or adjust, and increment changed batch versions.
5. Store movements and the successful request response, then commit together.

All write paths for an ingredient use the same ingredient lock. This serializes concurrent consumption, receipts, wastage, and reconciliation updates for that ingredient, including batches that did not yet exist when a request arrived. Separate ingredients can be updated independently. Requests touch one ingredient at a time, simplifying lock ordering.

The transaction uses PostgreSQL's default READ COMMITTED isolation. Balance queries run after acquiring the ingredient lock, so a waiting transaction sees the committed changes from the preceding operation. A validation failure rolls back all changes. Concurrency tests must use separate database connections against PostgreSQL.

Advisory hash collisions only serialize unrelated request keys; exact key comparison in the stored request table preserves correctness. Keys are scoped globally within this application's database. Fingerprints include the operation and validated payload. Changing the endpoint or payload while reusing a successful key returns a conflict. Only successful results are retained; failed requests can be corrected and retried. Keys have no automatic expiry in this portfolio version.

## FEFO allocation

Under the ingredient lock, find batches with positive balances and expiry dates on or after the business date. Sort by expiry, receipt date, and ID. Verify the sum covers the requested quantity before adding any movements. Walk the sorted list, consuming the smaller of the remaining request and each batch balance. All allocation movements share an operation identifier and commit together.

## Reconciliation

A preview stores the expected balance and batch version, physical count, discrepancy, and reason. Confirmation acquires the same ingredient lock and checks the stored version against the current batch version. A mismatch rejects the preview even if later stock changes happen to restore the same balance. Create a fresh preview after reviewing the current stock and count.

A valid confirmation inserts the signed discrepancy as an adjustment movement. Zero discrepancy marks the record confirmed without creating a zero movement. A confirmed reconciliation cannot apply another adjustment, including when the confirmation is retried with a new key.

## Reporting

Current balances, low-stock alerts, and expiry cards are current snapshots. Date filters affect wastage and confirmed-reconciliation metrics, not those stock snapshots. Wastage quantities are shown as positive amounts; reconciliation reports include both signed net adjustment and absolute discrepancy. Unit groups are separate. Movement history is paginated and filterable.

## Local-use boundary

There is no login, user ownership, tenant isolation, or external deployment configuration. Bind to `127.0.0.1`. The demo has one application operator; multiple browser requests are still handled safely. Public use would require authentication, authorization, network hardening, backups, observability, and operational review. Database backups and correction workflows are outside this version.

## Tradeoffs

- Ledger sums simplify auditability, at the cost of more aggregation work as history grows.
- An ingredient-level lock favors clear correctness over maximum write throughput.
- Fixed units avoid implicit conversions and mixed-precision errors.
- Separate preview/confirmation lets the operator review adjustments, with version checks preventing outdated counts from silently overwriting later movements.
- The frontend has no build tooling; the backend and SQL workflows are the focus.
