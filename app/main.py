from contextlib import asynccontextmanager, contextmanager
from datetime import date, timedelta
import logging
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker
from starlette.exceptions import HTTPException

from .config import Settings
from . import domain as D, schemas as S
from .models import Batch, Ingredient, Reconciliation, StockMovement

PAGE = Annotated[int, Query(ge=1)]
SIZE = Annotated[int, Query(ge=1, le=100)]
KEY = Annotated[
    str, Header(alias="Idempotency-Key", pattern=r"^[A-Za-z0-9._:-]{8,128}$")
]


def get_session(request: Request):
    with request.app.state.session_factory() as session:
        yield session


DB = Annotated[Session, Depends(get_session)]


@contextmanager
def write_transaction(s):
    with s.begin():
        s.execute(text("SELECT set_config('lock_timeout', '10s', true)"))
        s.execute(text("SELECT set_config('statement_timeout', '30s', true)"))
        yield


def error(status, code, message, details=None):
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details or []}},
    )


def page_result(s, q, page, size, render):
    total = s.scalar(select(func.count()).select_from(q.order_by(None).subquery()))
    rows = s.execute(q.offset((page - 1) * size).limit(size)).all()
    return {
        "items": [render(*row) for row in rows],
        "total": total,
        "page": page,
        "page_size": size,
    }


def date_filter(q, column, start, end):
    if start and end and start > end:
        D.fail(422, "invalid_date_range", "Start date must be on or before end date.")
    if start:
        q = q.where(column >= start)
    if end:
        q = q.where(column <= end)
    return q


def create_app(settings: Settings | None = None):
    config = settings or Settings()
    engine = create_engine(
        config.database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        connect_args={"connect_timeout": 5},
        isolation_level="READ COMMITTED",
    )

    @asynccontextmanager
    async def lifespan(app):
        yield
        engine.dispose()

    app = FastAPI(
        title="Canteen Inventory and Reconciliation",
        version="1.0.0",
        lifespan=lifespan,
        description="Local portfolio application. Quantities are exact decimal strings (up to 3 places). Stock writes require Idempotency-Key. No authentication; run on localhost only.",
        responses={code: {"model": S.ErrorResponse} for code in (404, 409, 422, 503)},
    )
    app.state.engine = engine
    app.state.session_factory = sessionmaker(engine, expire_on_commit=False)
    app.state.settings = config

    @app.exception_handler(D.DomainError)
    async def domain_error(request, exc):
        return error(exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        details = [
            {"field": ".".join(str(x) for x in e["loc"]), "message": e["msg"]}
            for e in exc.errors()
        ]
        return error(
            422, "validation_error", "Check the highlighted request fields.", details
        )

    @app.exception_handler(IntegrityError)
    async def integrity_error(request, exc):
        # Do not expose SQL, parameters or connection details in responses.
        return error(
            409,
            "record_conflict",
            "This name or batch reference already exists, or the change conflicts with stored records.",
        )

    @app.exception_handler(OperationalError)
    async def database_unavailable(request, exc):
        return error(
            503,
            "database_unavailable",
            "The database is unavailable or busy. Retry with the same Idempotency-Key.",
        )

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        logging.getLogger("canteen").error(
            "Unexpected error type: %s", type(exc).__name__
        )
        return error(
            500,
            "internal_error",
            "An unexpected error occurred. Check the server log and retry with the same request key if needed.",
        )

    @app.get("/health", tags=["System"])
    def health(s: DB):
        try:
            s.execute(text("SELECT 1"))
            # A reachable server is insufficient if setup has not migrated the schema.
            s.execute(select(Ingredient.id).limit(1))
        except (OperationalError, ProgrammingError):
            return error(
                503,
                "database_not_ready",
                "Database connection or schema unavailable. Check configuration and run alembic upgrade head.",
            )
        return {"status": "ok", "database": "ok"}

    @app.get("/api/config", tags=["System"])
    def configuration():
        return {"business_date": config.today(), "timezone": config.canteen_timezone}

    @app.post(
        "/api/ingredients",
        status_code=201,
        response_model=S.IngredientOut,
        tags=["Ingredients"],
    )
    def ingredient_create(p: S.IngredientCreate, s: DB):
        with write_transaction(s):
            result = D.create_ingredient(s, p, config.today())
        return result

    @app.patch(
        "/api/ingredients/{id}", response_model=S.IngredientOut, tags=["Ingredients"]
    )
    def ingredient_update(id: int, p: S.IngredientUpdate, s: DB):
        with write_transaction(s):
            result = D.update_ingredient(s, id, p, config.today())
        return result

    @app.get("/api/ingredients", response_model=S.IngredientPage, tags=["Ingredients"])
    def ingredients(
        s: DB, page: PAGE = 1, page_size: SIZE = 25, search: str | None = None
    ):
        q = select(Ingredient).order_by(Ingredient.normalized_name, Ingredient.id)
        if search:
            q = q.where(
                Ingredient.normalized_name.contains(
                    search.strip().casefold(), autoescape=True
                )
            )
        return page_result(
            s, q, page, page_size, lambda i: D.ingredient_view(s, i, config.today())
        )

    @app.get(
        "/api/ingredients/{id}", response_model=S.IngredientOut, tags=["Ingredients"]
    )
    def ingredient(id: int, s: DB):
        return D.ingredient_view(s, D.get_ingredient(s, id), config.today())

    def write_once(s, key, operation, p, status, action):
        with write_transaction(s):
            result, code, replayed = D.idempotent(s, key, operation, p, status, action)
        return JSONResponse(
            result,
            status_code=code,
            headers={"Idempotency-Replayed": str(replayed).lower()},
        )

    @app.post(
        "/api/batches", status_code=201, response_model=S.BatchOut, tags=["Batches"]
    )
    def batch_create(p: S.BatchCreate, s: DB, idempotency_key: KEY):
        return write_once(
            s,
            idempotency_key,
            "POST /batches",
            p,
            201,
            lambda: D.receive(s, p, config.today()),
        )

    @app.get("/api/batches", response_model=S.BatchPage, tags=["Batches"])
    def batches(
        s: DB,
        page: PAGE = 1,
        page_size: SIZE = 25,
        ingredient_id: int | None = None,
        status: Literal["available", "expiring", "expired"] | None = None,
    ):
        today = config.today()
        totals = D.balance_subquery()
        q = (
            select(Batch, Ingredient, func.coalesce(totals.c.balance, 0))
            .join(Ingredient)
            .outerjoin(totals, totals.c.batch_id == Batch.id)
        )
        if ingredient_id is not None:
            q = q.where(Batch.ingredient_id == ingredient_id)
        if status:
            q = q.where(totals.c.balance > 0)
            if status == "expired":
                q = q.where(Batch.expiry_date < today)
            else:
                q = q.where(Batch.expiry_date >= today)
                if status == "expiring":
                    q = q.where(Batch.expiry_date <= today + timedelta(days=7))
        q = q.order_by(Batch.expiry_date, Batch.received_date, Batch.id)
        return page_result(
            s, q, page, page_size, lambda b, i, qty: D.batch_view(s, b, i, today, qty)
        )

    @app.get("/api/batches/{id}", response_model=S.BatchOut, tags=["Batches"])
    def batch(id: int, s: DB):
        b, i = D.get_batch(s, id)
        return D.batch_view(s, b, i, config.today())

    @app.post(
        "/api/consumptions",
        status_code=201,
        response_model=S.OperationOut,
        tags=["Stock operations"],
    )
    def consumption(p: S.ConsumptionCreate, s: DB, idempotency_key: KEY):
        return write_once(
            s,
            idempotency_key,
            "POST /consumptions",
            p,
            201,
            lambda: D.consume(s, p, config.today()),
        )

    @app.post(
        "/api/wastages",
        status_code=201,
        response_model=S.OperationOut,
        tags=["Stock operations"],
    )
    def wastage(p: S.WastageCreate, s: DB, idempotency_key: KEY):
        return write_once(
            s,
            idempotency_key,
            "POST /wastages",
            p,
            201,
            lambda: D.waste(s, p, config.today()),
        )

    @app.post(
        "/api/reconciliations",
        status_code=201,
        response_model=S.ReconciliationOut,
        tags=["Reconciliations"],
    )
    def reconciliation_create(p: S.ReconciliationCreate, s: DB, idempotency_key: KEY):
        return write_once(
            s,
            idempotency_key,
            "POST /reconciliations",
            p,
            201,
            lambda: D.preview_reconciliation(s, p),
        )

    @app.post(
        "/api/reconciliations/{id}/confirm",
        response_model=S.ReconciliationOut,
        tags=["Reconciliations"],
    )
    def reconciliation_confirm(id: int, p: S.Confirm, s: DB, idempotency_key: KEY):
        return write_once(
            s,
            idempotency_key,
            f"POST /reconciliations/{id}/confirm",
            p,
            200,
            lambda: D.confirm_reconciliation(s, id, config.today()),
        )

    @app.get(
        "/api/reconciliations",
        response_model=S.ReconciliationPage,
        tags=["Reconciliations"],
    )
    def reconciliations(
        s: DB,
        page: PAGE = 1,
        page_size: SIZE = 25,
        status: Literal["pending", "confirmed"] | None = None,
    ):
        q = select(Reconciliation).order_by(Reconciliation.id.desc())
        if status:
            q = q.where(Reconciliation.status == status)
        return page_result(s, q, page, page_size, lambda r: D.reconciliation_view(s, r))

    @app.get("/api/movements", response_model=S.MovementPage, tags=["History"])
    def movements(
        s: DB,
        page: PAGE = 1,
        page_size: SIZE = 25,
        ingredient_id: int | None = None,
        batch_id: int | None = None,
        kind: S.Kind | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ):
        q = (
            select(StockMovement, Batch, Ingredient)
            .join(Batch, StockMovement.batch_id == Batch.id)
            .join(Ingredient, Batch.ingredient_id == Ingredient.id)
        )
        if ingredient_id is not None:
            q = q.where(Ingredient.id == ingredient_id)
        if batch_id is not None:
            q = q.where(Batch.id == batch_id)
        if kind:
            q = q.where(StockMovement.kind == kind)
        q = date_filter(q, StockMovement.business_date, start_date, end_date).order_by(
            StockMovement.id.desc()
        )
        return page_result(s, q, page, page_size, D.movement_view)

    @app.get("/api/dashboard", response_model=S.DashboardOut, tags=["Dashboard"])
    def dashboard(
        s: DB,
        start_date: date | None = None,
        end_date: date | None = None,
        ingredient_id: int | None = None,
    ):
        today = config.today()
        iq = select(Ingredient).order_by(Ingredient.normalized_name)
        bq = select(Batch, Ingredient).join(Ingredient)
        rq = select(Reconciliation).join(Batch)
        if ingredient_id is not None:
            iq = iq.where(Ingredient.id == ingredient_id)
            bq = bq.where(Ingredient.id == ingredient_id)
            rq = rq.where(Batch.ingredient_id == ingredient_id)
        items = [D.ingredient_view(s, i, today) for i in s.scalars(iq)]
        batch_items = [D.batch_view(s, b, i, today) for b, i in s.execute(bq)]
        wq = (
            select(
                Ingredient.unit, (-func.sum(StockMovement.quantity)).label("quantity")
            )
            .select_from(StockMovement)
            .join(Batch)
            .join(Ingredient)
            .where(StockMovement.kind == "wastage")
        )
        cq = (
            select(
                Ingredient.unit,
                func.sum(
                    Reconciliation.counted_quantity - Reconciliation.expected_quantity
                ),
                func.sum(
                    func.abs(
                        Reconciliation.counted_quantity
                        - Reconciliation.expected_quantity
                    )
                ),
                func.count(),
            )
            .select_from(Reconciliation)
            .join(Batch)
            .join(Ingredient)
            .where(Reconciliation.status == "confirmed")
        )
        if ingredient_id is not None:
            wq = wq.where(Ingredient.id == ingredient_id)
            cq = cq.where(Ingredient.id == ingredient_id)
        wq = (
            date_filter(wq, StockMovement.business_date, start_date, end_date)
            .group_by(Ingredient.unit)
            .order_by(Ingredient.unit)
        )
        cq = (
            date_filter(cq, Reconciliation.confirmed_date, start_date, end_date)
            .group_by(Ingredient.unit)
            .order_by(Ingredient.unit)
        )
        return S.DashboardOut(
            business_date=today,
            timezone=config.canteen_timezone,
            ingredient_count=len(items),
            batch_count=len(batch_items),
            low_stock=[i for i in items if i.is_low_stock],
            expiring_batches=sorted(
                [
                    b
                    for b in batch_items
                    if b.balance > 0
                    and today <= b.expiry_date <= today + timedelta(days=7)
                ],
                key=lambda b: (b.expiry_date, b.id),
            ),
            expired_batches=[b for b in batch_items if b.balance > 0 and b.is_expired],
            wastage_by_unit=[
                S.WasteTotal(unit=u, quantity=q) for u, q in s.execute(wq)
            ],
            reconciliation_by_unit=[
                S.ReconciliationTotal(
                    unit=u, net_adjustment=n, absolute_discrepancy=a, count=c
                )
                for u, n, a, c in s.execute(cq)
            ],
            pending_reconciliations=s.scalar(
                select(func.count()).select_from(
                    rq.where(Reconciliation.status == "pending").subquery()
                )
            ),
        )

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(static / "index.html")

    return app
