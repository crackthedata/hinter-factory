from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HINTER_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/hinter.db"
    cors_origins: str = "http://localhost:3000"


def get_settings() -> Settings:
    return Settings()


def ensure_sqlite_parent_dir(url: str) -> None:
    if not url.startswith("sqlite"):
        return
    raw = url.removeprefix("sqlite:///")
    if raw == ":memory:" or raw.startswith(":"):
        return
    path = Path(raw)
    if not path.is_absolute():
        path = Path(".") / path
    path.parent.mkdir(parents=True, exist_ok=True)
