from fastapi import APIRouter, Request

from app.domain.models import ApiError, Contract, StateResponse

router = APIRouter()


class HealthResponse(Contract):
    status: str
    database: str
    stage: str


@router.get("/health", response_model=HealthResponse, responses={503: {"model": ApiError}})
def health(request: Request):
    request.app.state.repository.check_ready()
    return HealthResponse(status="ok", database="ready", stage="1-foundation")


@router.get("/api/state", response_model=StateResponse, responses={503: {"model": ApiError}})
def state(request: Request):
    """Read the persisted initial state. Live simulation is introduced in stage 2."""
    return request.app.state.repository.current_state()
