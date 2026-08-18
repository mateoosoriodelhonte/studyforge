"""Configuration defaults, validation and derived values."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from studyforge.config import AIProvider, Environment, LogFormat, Settings, get_settings


class TestDefaults:
    def test_runs_with_no_environment_at_all(self) -> None:
        """A fresh clone with no .env must produce a usable configuration."""
        settings = Settings()
        assert settings.ai_provider is AIProvider.NONE
        assert settings.environment is Environment.DEVELOPMENT
        assert settings.log_format is LogFormat.CONSOLE
        assert settings.max_upload_mb == 20
        assert settings.database_url  # derived, never empty

    def test_ai_is_off_by_default(self) -> None:
        assert not Settings().ai_enabled

    def test_ollama_counts_as_enabled(self) -> None:
        assert Settings(ai_provider=AIProvider.OLLAMA).ai_enabled

    def test_derives_a_sqlite_url_under_the_data_dir(self) -> None:
        settings = Settings(data_dir=Path("/srv/sf"))
        assert settings.database_url == "sqlite+pysqlite:////srv/sf/studyforge.db"
        assert settings.is_sqlite

    def test_an_explicit_database_url_is_respected(self) -> None:
        url = "postgresql+psycopg://localhost/studyforge"
        settings = Settings(database_url=url)
        assert settings.database_url == url
        assert not settings.is_sqlite

    def test_uploads_live_under_the_data_dir(self) -> None:
        assert Settings(data_dir=Path("/srv/sf")).uploads_dir == Path("/srv/sf/uploads")


class TestValidation:
    @pytest.mark.parametrize("level", ["debug", "Info", "WARNING"])
    def test_log_level_is_normalised_to_upper_case(self, level: str) -> None:
        assert Settings(log_level=level).log_level == level.upper()

    def test_rejects_a_nonsense_log_level(self) -> None:
        with pytest.raises(ValidationError, match="log_level"):
            Settings(log_level="chatty")

    @pytest.mark.parametrize("mb", [0, -1, 500])
    def test_rejects_an_out_of_range_upload_limit(self, mb: int) -> None:
        with pytest.raises(ValidationError):
            Settings(max_upload_mb=mb)

    def test_rejects_an_unknown_ai_provider(self) -> None:
        with pytest.raises(ValidationError):
            Settings(ai_provider="gpt-9")  # type: ignore[arg-type]

    @pytest.mark.parametrize("seconds", [0, -5, 601])
    def test_rejects_an_unreasonable_ai_timeout(self, seconds: float) -> None:
        with pytest.raises(ValidationError):
            Settings(ai_timeout_seconds=seconds)


class TestDerivedValues:
    def test_upload_limit_converts_to_bytes(self) -> None:
        assert Settings(max_upload_mb=7).max_upload_bytes == 7 * 1024 * 1024

    def test_flags_the_insecure_default_secret(self) -> None:
        assert Settings().secret_key_is_insecure_default
        assert not Settings(secret_key="a-real-one").secret_key_is_insecure_default

    def test_ensure_directories_is_idempotent(self, tmp_path: Path) -> None:
        settings = Settings(data_dir=tmp_path / "d")
        settings.ensure_directories()
        settings.ensure_directories()
        assert settings.data_dir.is_dir()
        assert settings.uploads_dir.is_dir()


class TestEnvironmentOverrides:
    def test_reads_from_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_PROVIDER", "ollama")
        monkeypatch.setenv("MAX_UPLOAD_MB", "5")
        monkeypatch.setenv("LOG_FORMAT", "json")
        settings = Settings()
        assert settings.ai_provider is AIProvider.OLLAMA
        assert settings.max_upload_mb == 5
        assert settings.log_format is LogFormat.JSON

    def test_unknown_environment_variables_are_ignored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("STUDYFORGE_SOMETHING_ELSE", "x")
        Settings()  # must not raise

    def test_get_settings_is_cached(self) -> None:
        assert get_settings() is get_settings()
