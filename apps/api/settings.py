# apps/api/settings.py
"""Pydantic Settings for FastAPI application.

This module defines a strict configuration loader using ``pydantic-settings``. It reads
environment variables from the ``.env`` file located at the repository root and validates
them against explicit Python types. Any missing or malformed variable will raise a
validation error at application start‑up, preventing the service from running with an
inconsistent configuration.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def resolve_env_file() -> Path:
    """Localiza el fichero ``.env`` sin romper fuera del layout del repositorio.

    En el repositorio ``settings.py`` vive en ``apps/api/``, asi que la raiz es
    ``parents[2]``. En la imagen de Docker el fichero esta en ``/app/`` y no
    hay mas niveles que ``/``: usar ``parents[2]`` ahi lanza ``IndexError`` y el
    contenedor de la API muere al arrancar.

    En Docker el ``.env`` no se copia a la imagen (los secretos no se hornean):
    ``docker compose`` lo inyecta como variables de entorno. Por eso, si no hay
    fichero, se devuelve una ruta inexistente y ``pydantic-settings`` la ignora
    sin fallar. Se puede forzar con la variable ``ENV_FILE``.
    """
    override = os.environ.get("ENV_FILE")
    if override:
        return Path(override)

    here = Path(__file__).resolve()
    if len(here.parents) >= 3:
        candidate = here.parents[2] / ".env"
        if candidate.is_file():
            return candidate
    return here.parent / ".env"


class Settings(BaseSettings):
    """Application configuration loaded from environment variables.

    The ``env_file`` points to the project root ``.env``. All fields are typed
    strictly; pydantic will coerce where safe and raise otherwise.
    """

    # Core API configuration
    DATABASE_URL: str = Field(..., description="PostgreSQL connection string")
    API_BASE_URL: Optional[str] = Field(
        default=None,
        description="Base URL for the FastAPI instance (only required when workers call the API over HTTPS)",
    )
    WORKER_API_TOKEN: str = Field(..., description="Secret token for worker authentication")
    CORS_ORIGINS: str = Field(
        default="http://localhost:3001,http://127.0.0.1:3001,http://localhost:3000",
        description="Comma separated list of allowed browser origins",
    )

    # Storage (S3 / R2) configuration
    S3_ENDPOINT_URL: str = Field(..., description="Endpoint URL for S3 compatible storage")
    S3_BUCKET: str = Field(..., description="Bucket name for media assets")
    S3_ACCESS_KEY_ID: str = Field(..., description="Access key ID for storage")
    S3_SECRET_ACCESS_KEY: str = Field(..., description="Secret access key for storage")

    # Optional third‑party keys
    HF_TOKEN: Optional[str] = Field(default=None, description="HuggingFace token for private spaces")
    SCRIPT_SPACE_URL: Optional[str] = Field(default=None, description="URL of the script generation Space")
    VOICE_STUDIO_URL: Optional[str] = Field(default=None, description="URL of the voice synthesis Space")
    YOUTUBE_API_KEY: Optional[str] = Field(default=None, description="YouTube Data API key")
    PUBLISHER_BASE_URL: Optional[str] = Field(
        default=None,
        description=(
            "URL del publisher-service. Si esta definida, al aprobar un video "
            "la API lo encola en el scheduler configurado."
        ),
    )
    STORAGE_PUBLIC_BASE_URL: Optional[str] = Field(
        default=None,
        description=(
            "Host publico del bucket de medios. Necesario para entregar la URL "
            "HTTPS del video al scheduler."
        ),
    )

    # Runtime configuration
    JOB_LEASE_SECONDS: int = Field(default=300, ge=60, description="Lease duration for a job")
    MAX_JOB_ATTEMPTS: int = Field(default=3, ge=1, description="Maximum retry attempts per job")
    MAX_ASSET_BYTES: int = Field(default=250 * 1024 * 1024, description="Maximum size for uploaded assets")

    # Enforce strict env file location and prevent secret leakage
    model_config = SettingsConfigDict(
        env_file=resolve_env_file(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        # The repository .env is shared by several consumers (postgres, mixpost,
        # dashboard, workers). Unknown keys must not abort the boot.
        extra="ignore",
    )

    @field_validator("API_BASE_URL")
    @classmethod
    def ensure_https(cls, v: Optional[str]) -> Optional[str]:
        # Una variable vacia equivale a "no configurada" (util en .env.example).
        if v is None or not v.strip():
            return None
        if not v.startswith("https://"):
            raise ValueError("API_BASE_URL must start with https://")
        return v.rstrip("/")

    @field_validator("DATABASE_URL")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v:
            raise ValueError("DATABASE_URL cannot be empty")
        return v

    @field_validator("S3_ENDPOINT_URL")
    @classmethod
    def endpoint_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("S3_ENDPOINT_URL must be a valid URL")
        return v.rstrip("/")


def get_settings() -> Settings:
    """Helper to instantiate settings and raise early if configuration is invalid."""
    try:
        return Settings()
    except ValidationError as exc:
        # Log the detailed validation errors before re‑raising.
        import logging

        logger = logging.getLogger(__name__)
        logger.error("Invalid application configuration: %s", exc)
        raise
