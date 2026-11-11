"""Configuracion del publisher-service.

Antes la configuracion se leia con ``os.environ.get(...)`` sueltos en
``main.py``, mientras que la API usa ``pydantic-settings``
(``apps/api/settings.py``). Aqui se centraliza con el mismo mecanismo: los
MISMOS nombres de variable, los MISMOS valores por defecto y validacion
explicita de lo que se leia a pelo.

Solo se leen variables de entorno del proceso: el despliegue las inyecta con
``env_file`` de docker compose, asi que el servicio arranca igual sin un
fichero ``.env``.

El modulo no se llama ``settings`` a proposito: el repo ya tiene
``apps/api/settings.py`` y los dos ficheros conviven en la misma suite de
tests, donde el nombre del modulo en ``sys.modules`` es global.
"""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Backends de publicacion soportados.
BACKENDS: tuple[str, ...] = ("open_dispatch", "mixpost")

# Origenes CORS por defecto. El dashboard corre en el puerto 3000 (Makefile y
# docker-compose.dev.yml), aunque hoy no llama al publisher desde el
# navegador: se incluye por si el panel lo hace, igual que hace la API.
DEFAULT_CORS_ORIGINS: str = "http://localhost:3001,http://localhost:3000"


class PublisherSettings(BaseSettings):
    """Configuracion del publisher-service leida del entorno."""

    PUBLISHER_BACKEND: str = Field(
        default="open_dispatch",
        description="Backend de publicacion: open_dispatch o mixpost.",
    )
    CORS_ORIGINS: str = Field(
        default=DEFAULT_CORS_ORIGINS,
        description="Orígenes permitidos por CORS, separados por comas.",
    )

    model_config = SettingsConfigDict(
        case_sensitive=False,
        # El .env del repo es compartido por varios consumidores: las claves
        # que no son del publisher no pueden abortar el arranque.
        extra="ignore",
    )

    @field_validator("PUBLISHER_BACKEND", mode="before")
    @classmethod
    def normalize_backend(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @property
    def cors_origins(self) -> list[str]:
        """Orígenes CORS ya normalizados (sin entradas vacías)."""
        return [
            origin.strip()
            for origin in self.CORS_ORIGINS.split(",")
            if origin.strip()
        ]


def get_settings() -> PublisherSettings:
    """Configuracion validada del publisher.

    El backend se valida aqui y no en el modelo para que un valor invalido no
    impida que el servicio arranque: se reporta como 503 en /v1/publish, que es
    lo que hacia la lectura directa de ``os.environ``.
    """
    settings = PublisherSettings()
    if settings.PUBLISHER_BACKEND not in BACKENDS:
        raise RuntimeError(
            f"PUBLISHER_BACKEND invalido: {settings.PUBLISHER_BACKEND!r}. "
            f"Usa {', '.join(BACKENDS)}."
        )
    return settings


def cors_origins() -> list[str]:
    """Orígenes CORS permitidos, sin validar el resto de la configuracion.

    El middleware CORS se registra al importar ``main``: un
    ``PUBLISHER_BACKEND`` invalido no debe impedir que el servicio levante.
    """
    return PublisherSettings().cors_origins
