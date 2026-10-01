from datetime import datetime
from typing import Literal

from pydantic import Field, SecretStr

from app.domain.models import Contract


class LoginRequest(Contract):
    username: str = Field(min_length=1, max_length=64, examples=["dispatcher"])
    # SecretStr keeps the password out of repr/logs; the 422 handler never echoes input.
    password: SecretStr = Field(min_length=1, max_length=1024)


class SessionInfo(Contract):
    username: str
    role: Literal["viewer", "dispatcher", "admin"]
    permissions: list[str]
    expires_at: datetime
    csrf_token: str = Field(description="Отправляйте в заголовке X-CSRF-Token в каждом POST/PATCH/DELETE. "
                                        "Храните только в памяти страницы; после перезагрузки — GET /api/me.")
