import logging
import asyncio
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg_pool import ConnectionPool, PoolTimeout
from starlette.exceptions import HTTPException

from app.api.routes import router
from app.api.live import router as live_router
from app.api.plans import router as plans_router
from app.api.history import router as history_router
from app.auth import CSRF_HEADER, PostgresSessionStore, install_auth
from app.storage.history import HistoryError
from app.runtime.planning import PlannerProcess
from app.runtime.coordinator import Coordinator, RuntimeUnavailable
from app.simulation.engine import SimulationError
from app.domain.models import ApiError
from app.settings import Settings
from app.storage.repository import NotInitialized, Repository

logger = logging.getLogger(__name__)


def error_response(status: int, code: str, message: str, details=None):
    return JSONResponse(status_code=status, content=ApiError(
        code=code, message=message, details=details or []
    ).model_dump(mode="json"))


def create_app(settings: Settings | None = None, repository=None, coordinator=None,
               sessions=None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.runtime = coordinator
        app.state.runtime_required = repository is None
        app.state.allowed_origins = settings.allowed_origins
        if repository is not None:
            app.state.repository = repository
            app.state.auth.store = sessions   # tests inject a double or a real PostgreSQL store
            if coordinator: await coordinator.start()
            yield
            if coordinator: await coordinator.stop()
            return
        pool = ConnectionPool(
            conninfo=settings.database_url.get_secret_value(),
            min_size=0, max_size=settings.db_pool_max_size, open=False,
            timeout=settings.db_timeout_s,
            kwargs={"connect_timeout": 3, "options": "-c statement_timeout=3000"},
        )
        pool.open()
        app.state.repository = Repository(pool)
        app.state.auth.store = PostgresSessionStore(pool)
        try:
            try:
                await asyncio.to_thread(app.state.repository.claim_owner)
                state, initial_plan = await asyncio.to_thread(app.state.repository.load_runtime)
                required = await asyncio.to_thread(app.state.repository.needs_replan,state.run_id)
                owner = Coordinator(app.state.repository,state,initial_plan,replan_required=required,
                    planner=PlannerProcess(settings.planner_timeout_s,settings.planner_budget_s))
                await owner.start()
                app.state.runtime = owner
            except (psycopg.Error,PoolTimeout,NotInitialized) as exc:
                logger.error("runtime_unavailable error_type=%s",type(exc).__name__)
                await asyncio.to_thread(app.state.repository.release_owner)
            yield
        finally:
            if app.state.runtime: await app.state.runtime.stop()
            await asyncio.to_thread(app.state.repository.release_owner)
            pool.close()

    app = FastAPI(title="Узел 12 — Backend", version="0.5.0", lifespan=lifespan,
                  description="Этапы 4–5: фоновые планы, принятие, история и CSV.")
    app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins,
                       allow_credentials=True, allow_methods=["GET","POST"],
                       allow_headers=["Content-Type", CSRF_HEADER], expose_headers=["Retry-After"])
    install_auth(app, settings)

    @app.exception_handler(RuntimeUnavailable)
    async def runtime_error(request: Request, exc: RuntimeUnavailable):
        return error_response(503,"SIMULATION_UNAVAILABLE",str(exc))

    @app.exception_handler(HistoryError)
    async def history_error(request: Request,exc: HistoryError):
        return error_response(exc.status,exc.code,exc.message)

    @app.exception_handler(SimulationError)
    async def simulation_error(request: Request, exc: SimulationError):
        details = exc.details if isinstance(exc.details,list) else []
        status = 422 if exc.code in ("INVALID_INPUT","INVALID_TIME") else 409
        return error_response(status,exc.code,exc.message,details)

    @app.exception_handler(NotInitialized)
    async def not_initialized(request: Request, exc: NotInitialized):
        return error_response(503, "NOT_INITIALIZED", "Выполните миграции и bootstrap базы данных.")

    async def database_error(request: Request, exc: Exception):
        # Do not leak connection strings, SQL or credentials into responses/logs.
        logger.error("database_unavailable path=%s error_type=%s", request.url.path, type(exc).__name__)
        return error_response(503, "DATABASE_UNAVAILABLE", "База данных недоступна или схема не подготовлена.")

    app.add_exception_handler(psycopg.Error, database_error)
    app.add_exception_handler(PoolTimeout, database_error)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = [{"loc": list(e["loc"]), "type": e["type"], "message": e["msg"]} for e in exc.errors()]
        return error_response(422, "VALIDATION_ERROR", "Проверьте поля запроса.", details)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, f"HTTP_{exc.status_code}")
        return error_response(exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception):
        # Unexpected failure: same ApiError shape as every other error, no traceback or data leak.
        logger.error("internal_error path=%s error_type=%s", request.url.path, type(exc).__name__)
        return error_response(500, "INTERNAL_ERROR", "Внутренняя ошибка сервера. Подробности — в журнале backend.")

    errors={code:{'model':ApiError} for code in (401,403,404,409,422,500,503)}
    for api_router in (router,live_router,plans_router,history_router):
        app.include_router(api_router,responses=errors)
    return app


app = create_app()
