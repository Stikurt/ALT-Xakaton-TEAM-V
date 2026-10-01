import logging
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg_pool import ConnectionPool, PoolTimeout
from starlette.exceptions import HTTPException

from app.api.routes import router
from app.domain.models import ApiError
from app.settings import Settings
from app.storage.repository import NotInitialized, Repository

logger = logging.getLogger(__name__)


def error_response(status: int, code: str, message: str, details=None):
    return JSONResponse(status_code=status, content=ApiError(
        code=code, message=message, details=details or []
    ).model_dump(mode="json"))


def create_app(settings: Settings | None = None, repository=None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if repository is not None:
            app.state.repository = repository
            yield
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
            yield
        finally:
            pool.close()

    app = FastAPI(title="Узел 12 — Backend", version="0.1.0", lifespan=lifespan,
                  description="Этап 1: контракты, PostgreSQL, health и чтение исходного состояния.")
    app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins,
                       allow_credentials=True, allow_methods=["GET"], allow_headers=["Content-Type"])

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
    return app


app = create_app()
