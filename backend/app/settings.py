from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    database_url: SecretStr = SecretStr("postgresql://localhost:5432/uzel12")
    allowed_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    db_pool_max_size: int = Field(default=4, ge=2, le=10)
    db_timeout_s: float = Field(default=3, gt=0, le=30)
    scenario_path: str = "shared/scenarios/one_train.json"

    # Stage 6 access control, see docs/auth.md. Values come only from the environment/.env.
    session_secret: SecretStr | None = None
    admin_password_hash: SecretStr | None = None
    dispatcher_password_hash: SecretStr | None = None
    viewer_password_hash: SecretStr | None = None
    session_ttl_s: int = Field(default=8 * 3600, ge=60, le=7 * 24 * 3600)
    # True for HTTPS. Plain-HTTP local development must opt out explicitly.
    session_cookie_secure: bool = True
    session_cookie_samesite: Literal["strict", "lax", "none"] = "strict"
    ws_session_recheck_s: float = Field(default=10, ge=0.05, le=300)

    @field_validator("allowed_origins")
    @classmethod
    def exact_origins(cls, value: list[str]) -> list[str]:
        # Credentialed CORS and the CSRF Origin check need an exact list, never a wildcard.
        result = []
        for origin in value:
            origin = origin.rstrip("/")
            if origin in ("*", "null") or "://" not in origin or "/" in origin.split("://", 1)[1]:
                raise ValueError("ALLOWED_ORIGINS must list exact origins like http://localhost:5173")
            result.append(origin)
        return result

    @model_validator(mode="after")
    def samesite_none_needs_secure(self):
        if self.session_cookie_samesite == "none" and not self.session_cookie_secure:
            raise ValueError("SESSION_COOKIE_SAMESITE=none requires SESSION_COOKIE_SECURE=true")
        return self
