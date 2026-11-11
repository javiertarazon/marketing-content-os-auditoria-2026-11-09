"""Publisher del Marketing Content OS.

Traduce una peticion de publicacion en una *ContentUnit* de Open-Dispatch
(https://github.com/Matthew-Selvam/Open-Dispatch, licencia MIT) y la encola.

Contrato:

    GET  /healthz          -> estado del servicio
    GET  /v1/integrations  -> plataformas publicables con la configuracion actual
    POST /v1/publish       -> encola la publicacion

El backend se elige con ``PUBLISHER_BACKEND``:

    open_dispatch (por defecto)  TikTok / Instagram / YouTube via Open-Dispatch
    mixpost                     via la API REST de Mixpost (Mixpost Pro o Cloud)

Con Open-Dispatch la peticion lleva la lista de plataformas::

    {"video_url": "https://cdn.../video.mp4",
     "title": "Tema del dia",
     "platforms": ["tiktok", "instagram", "youtube"],
     "publish_at": "2026-11-05T15:30:00-04:00",
     "metadata": {"content": "Descripcion", "settings": {"tags": ["marketing"]}}}

Con Mixpost la peticion lleva el identificador de la cuenta (``integration_id``).
"""

import os
from datetime import datetime

import httpx
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, HttpUrl, field_validator

from adapters import mixpost_adapter, open_dispatch_adapter

BACKENDS = ("open_dispatch", "mixpost")

app = FastAPI(title="Marketing Content OS Publisher", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.environ.get(
            "CORS_ORIGINS", "http://localhost:3001"
        ).split(",")
        if origin.strip()
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


def backend() -> str:
    """Backend de publicacion configurado."""
    selected = os.environ.get("PUBLISHER_BACKEND", "open_dispatch").strip().lower()
    if selected not in BACKENDS:
        raise RuntimeError(
            f"PUBLISHER_BACKEND invalido: {selected!r}. Usa {', '.join(BACKENDS)}."
        )
    return selected


def scheduler_error_detail(exc: httpx.HTTPStatusError) -> str:
    """Mensaje con el detalle que devuelve el scheduler.

    Open-Dispatch responde 400 con {"detail": {"errors": [...]}} cuando la
    ContentUnit no valida. Sin surfacing de ese detalle, el unico sintoma
    seria un HTTP 400 sin explicar por que.
    """
    detail = ""
    try:
        payload = exc.response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        raw = payload.get("detail")
        if isinstance(raw, dict):
            raw = raw.get("errors") or raw
        if isinstance(raw, list):
            detail = "; ".join(str(item) for item in raw)
        elif raw:
            detail = str(raw)
    elif isinstance(payload, str):
        detail = payload
    base = (
        "El scheduler de publicacion rechazo la solicitud con "
        f"HTTP {exc.response.status_code}"
    )
    return f"{base}: {detail}" if detail else base


class PublishRequest(BaseModel):
    video_url: HttpUrl
    title: str = Field(min_length=1, max_length=500)
    platforms: list[str] = Field(
        default_factory=list,
        max_length=20,
        description=(
            "Plataformas destino de Open-Dispatch: tiktok, instagram y/o "
            "youtube (opcionalmente 'plataforma:cuenta')."
        ),
    )
    platform: str | None = Field(
        default=None,
        max_length=100,
        description=(
            "Alias de una sola plataforma. Se conserva por compatibilidad; "
            "si 'platforms' esta vacio se usa este valor."
        ),
    )
    integration_id: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "Id de la cuenta de Mixpost (campo `id` de GET /v1/integrations). "
            "Solo se usa con PUBLISHER_BACKEND=mixpost."
        ),
    )
    publish_at: datetime | None = None
    metadata: dict = Field(default_factory=dict)

    @field_validator("publish_at")
    @classmethod
    def publish_at_must_include_timezone(
        cls, value: datetime | None
    ) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("publish_at debe incluir zona horaria")
        return value


def _requested_platforms(request: PublishRequest) -> list[str]:
    """Plataformas pedidas, normalizando el alias `platform`."""
    platforms = list(request.platforms)
    if not platforms and request.platform:
        platforms = [request.platform]
    return platforms


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "backend": backend()}


@app.get("/v1/integrations")
async def integrations() -> list[dict]:
    try:
        if backend() == "mixpost":
            return await mixpost_adapter.list_accounts()
        return await open_dispatch_adapter.list_accounts()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=scheduler_error_detail(exc),
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo conectar con el scheduler de publicacion",
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@app.post("/v1/publish")
async def publish(request: PublishRequest) -> dict:
    selected = backend()
    payload = request.model_dump(mode="json")

    try:
        if selected == "mixpost":
            if not request.integration_id:
                raise ValueError(
                    "Con PUBLISHER_BACKEND=mixpost hace falta 'integration_id' "
                    "con el id de la cuenta"
                )
            payload["platform"] = request.platform or ""
            return await mixpost_adapter.create_post(payload)

        payload["platforms"] = _requested_platforms(request)
        return await open_dispatch_adapter.dispatch_content(payload)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=scheduler_error_detail(exc),
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo conectar con el scheduler de publicacion",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
