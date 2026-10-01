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


def create_app(settings: Settings | None = None, repository=None, coordinator=None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.runtime = coordinator
        app.state.runtime_required = repository is None
        app.state.allowed_origins = settings.allowed_origins
        if repository is not None:
            app.state.repository = repository
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
        try:
            try:
                await asyncio.to_thread(app.state.repository.claim_owner)
                state, initial_plan = await asyncio.to_thread(app.state.repository.load_runtime)
                owner = Coordinator(app.state.repository,state,initial_plan)
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

    app = FastAPI(title="Узел 12 — Backend", version="0.2.0", lifespan=lifespan,
                  description="Этап 2: общий движок, PostgreSQL, команды управления и WebSocket.")
    app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins,
                       allow_credentials=True, allow_methods=["GET","POST"], allow_headers=["Content-Type"])

    @app.exception_handler(RuntimeUnavailable)
    async def runtime_error(request: Request, exc: RuntimeUnavailable):
        return error_response(503,"SIMULATION_UNAVAILABLE",str(exc))

    @app.exception_handler(SimulationError)
    async def simulation_error(request: Request, exc: SimulationError):
        details = exc.details if isinstance(exc.details,list) else []
        return error_response(422 if exc.code == "INVALID_INPUT" else 409,exc.code,exc.message,details)

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
        return error_response(exc.status_code, f"HTTP_{exc.status_code}", str(exc.detail))

    app.include_router(router)
    app.include_router(live_router)
    return app


app = create_app()
