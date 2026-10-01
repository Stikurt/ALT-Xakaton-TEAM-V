from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = ROOT.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    database_url: SecretStr = SecretStr("postgresql://localhost:5432/uzel12")
    allowed_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    db_pool_max_size: int = Field(default=4, ge=1, le=10)
    db_timeout_s: float = Field(default=3, gt=0, le=30)
