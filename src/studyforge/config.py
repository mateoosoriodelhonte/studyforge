"""Environment-driven application configuration.

Every setting has a safe default so that ``uv run studyforge serve`` works on a
fresh clone with no ``.env`` file. See ``.env.example`` for the documented set.
"""

from __future__ import annotations

import enum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AIProvider(enum.StrEnum):
    """Which AI backend, if any, augments the deterministic pipeline."""

    NONE = "none"
    OLLAMA = "ollama"


class Environment(enum.StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class LogFormat(enum.StrEnum):
    CONSOLE = "console"
    JSON = "json"


_INSECURE_DEFAULT_SECRET = "dev-insecure-change-me"  # noqa: S105


class Settings(BaseSettings):
    """Application settings, read from the environment and an optional ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Core ---
    environment: Environment = Environment.DEVELOPMENT
    data_dir: Path = Path("./data")
    database_url: str = ""
    secret_key: str = _INSECURE_DEFAULT_SECRET

    # --- Uploads ---
    max_upload_mb: int = Field(default=20, ge=1, le=200)

    # --- Logging ---
    log_level: str = "INFO"
    log_format: LogFormat = LogFormat.CONSOLE

    # --- AI ---
    ai_provider: AIProvider = AIProvider.NONE
    ai_model: str = "llama3.2"
    ai_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    ollama_base_url: str = "http://localhost:11434"

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = value.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}, got {value!r}")
        return upper

    @model_validator(mode="after")
    def _derive_database_url(self) -> Settings:
        if not self.database_url:
            db_path = self.data_dir / "studyforge.db"
            # Keep the URL relative when the data dir is relative; absolute otherwise.
            self.database_url = f"sqlite+pysqlite:///{db_path}"
        return self

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def uploads_dir(self) -> Path:
        """Directory holding uploaded source documents, one flat namespace."""
        return self.data_dir / "uploads"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def ai_enabled(self) -> bool:
        return self.ai_provider is not AIProvider.NONE

    @property
    def secret_key_is_insecure_default(self) -> bool:
        return self.secret_key == _INSECURE_DEFAULT_SECRET

    def ensure_directories(self) -> None:
        """Create the local data directories. Safe to call repeatedly."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton."""
    return Settings()
