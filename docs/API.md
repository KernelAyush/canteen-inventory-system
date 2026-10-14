# API reference

The local default base URL is `http://127.0.0.1:8000`. `/docs` provides interactive OpenAPI documentation and `/openapi.json` provides the machine-readable schema. Operational endpoints use the `/api` prefix.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Check server/database connectivity |
| GET | `/api/config` | Current business date and timezone |
| POST | `/api/ingredients` | Add an ingredient |
| GET | `/api/ingredients` | Paginated ingredients with balances; optional `search` |
| GET | `/api/ingredients/{id}` | Ingredient and current balances |
| PATCH | `/api/ingredients/{id}` | Update name, threshold, or unit when allowed |
| POST | `/api/batches` | Receive a batch; idempotency key required |
| GET | `/api/batches` | Paginated batches; optional `ingredient_id`, `status` |
| GET | `/api/batches/{id}` | Batch balance and version |
| POST | `/api/consumptions` | Consume by FEFO; idempotency key required |
| POST | `/api/wastages` | Record batch wastage; idempotency key required |
| POST | `/api/reconciliations` | Create a counted-stock preview; idempotency key required |
| GET | `/api/reconciliations` | Paginated previews; optional `status=pending|confirmed` |
| POST | `/api/reconciliations/{id}/confirm` | Apply a valid preview; idempotency key required |
| GET | `/api/movements` | Paginated movement history with filters |
| GET | `/api/dashboard` | Current alerts and filtered reporting metrics |

## Common conventions

Send JSON with `Content-Type: application/json`. Quantities are decimal strings such as `"12.500"`, with at most three decimal places. Units are `kg`, `litre`, and `piece`. Dates use `YYYY-MM-DD`. Optional notes can be omitted.

Paginated responses contain `items`, `total`, `page`, and `page_size`. `page` starts at 1; `page_size` is 1–100 (default 25). Ingredients sort by name; batches sort in FEFO order. History and reconciliation lists show newest IDs first.

Errors have this shape:

```json
{"error":{"code":"...","message":"Human-readable explanation","details":[]}}
```

Successful reads and updates return 200; creation returns 201. Invalid fields return 422, missing records 404, and stock or state conflicts 409. The exact generated response schemas are in `/docs`.

## Request examples

Create an ingredient:

```json
{"name":"Rice","unit":"kg","low_stock_threshold":"5.000"}
```

Receive a batch:

```json
{"ingredient_id":1,"reference":"RICE-A","quantity":"10.000","received_date":"2026-10-14","expiry_date":"2026-10-20","note":"Delivery"}
```

The receipt example uses illustrative dates. Use actual business-date-appropriate values when running it.

Consume:

```json
{"ingredient_id":1,"quantity":"12.000","note":"Lunch preparation"}
```

Record wastage:

```json
{"batch_id":2,"quantity":"1.000","reason":"Spilled during handling"}
```

Create reconciliation preview:

```json
{"batch_id":2,"counted_quantity":"11.500","reason":"Physical closing count"}
```

Confirm with `POST /api/reconciliations/{id}/confirm`, body `{}`, and a new idempotency key.

## Retry contract

Generate one `Idempotency-Key` for a logical submission and retain it while retrying that same submission. Keys must contain 8–128 ASCII letters, digits, periods, underscores, colons, or hyphens. Example: `Idempotency-Key: 6c018e55-a42b-47f6-a4f3-4da0b179ba38`.

- Same successful key and equivalent validated payload: return the saved status/body; no extra movement.
- Same successful key with a different operation or payload: 409.
- Failed operation: rollback; no successful key record retained.
- Keys are global to the database and have no expiry in this version.

Retries return the original result even when the current stock or date has subsequently changed. To request a genuinely new operation, generate a new key. Ingredient CRUD does not use idempotency keys; ingredient uniqueness prevents duplicate names.

## Filters

- Batches: `ingredient_id`, `status=available|expiring|expired`. Expiring is the next seven days, including today.
- Movements: `ingredient_id`, `batch_id`, `kind=receipt|consumption|wastage|adjustment`, `start_date`, `end_date`.
- Dashboard: `ingredient_id`, `start_date`, `end_date`. Date ranges are inclusive and affect wastage/confirmed-reconciliation metrics; stock cards remain current.
- Reconciliations: `status=pending|confirmed`.

Example: `/api/movements?ingredient_id=1&kind=wastage&page=1&page_size=25`.
