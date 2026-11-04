"""Cấu hình tập trung, đọc từ biến môi trường / file .env ở thư mục gốc dự án."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]  # .../permission-aware-rag-wiki


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # PostgreSQL
    postgres_user: str = "wiki"
    postgres_password: str = "wiki_dev_password"
    postgres_db: str = "wiki"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    # OpenFGA
    openfga_api_url: str = "http://localhost:8080"
    openfga_store_id: str = ""
    openfga_model_id: str = ""

    # LLM / Embedding
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = Field(default=1024, ge=1)

    # App
    app_env: str = "dev"
    log_level: str = "INFO"

    # Pool
    db_pool_min: int = 1
    db_pool_max: int = 10

    # Ingest & Paths
    actor: str = "system-ingest"
    max_chunk_chars: int = 1500
    embedding_batch_size: int = 16
    embedding_device: str | None = None

    @property
    def is_prod(self) -> bool:
        return self.app_env.lower() in ("prod", "production")

    @property
    def data_dir(self) -> Path:
        return ROOT_DIR / "data"

    @property
    def documents_dir(self) -> Path:
        return ROOT_DIR / "data" / "documents"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()