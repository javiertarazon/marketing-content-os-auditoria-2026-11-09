"""Adaptador de Mixpost Lite para publicar vídeos en redes sociales.

Mixpost **no** expone una API compatible con Postiz: tiene su propia API REST.

    Base       {scheme}://{host}/{corePath}/api     (Lite usa corePath "mixpost")
    Auth       Authorization: Bearer <clave personal de API>
    Cuentas    GET  /workspaces
               GET  /{workspace}/accounts
    Medios     POST /{workspace}/media              (multipart, campo "file")
    Publicar   POST /{workspace}/posts

La clave se genera en la página **API** del panel de Mixpost y se muestra una sola
vez. No es la ``APP_KEY`` de Laravel: esa sólo la usa el contenedor de Mixpost.

Contrato verificado contra la especificación OpenAPI oficial de Mixpost
(https://docs.mixpost.app/api/), no contra la API de Postiz.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

MAX_VIDEO_BYTES = int(os.environ.get("MIXPOST_MAX_VIDEO_BYTES", 250_000_000))

# Proveedores de Mixpost capaces de publicar vídeo. La clave es el `provider`
# de la cuenta, no el nombre de la plataforma.
VIDEO_PROVIDERS = {
    "youtube",
    "tiktok",
    "instagram",
    "instagram_standalone",
    "facebook_page",
}

# Traduce los nombres de plataforma que usa el resto del proyecto (y el
# dashboard) a los identificadores de proveedor de Mixpost.
PROVIDER_ALIASES = {
    "youtube": "youtube",
    "tiktok": "tiktok",
    "instagram": "instagram",
    "instagram-standalone": "instagram_standalone",
    "instagram_standalone": "instagram_standalone",
    "facebook": "facebook_page",
    "facebook-page": "facebook_page",
    "facebook_page": "facebook_page",
}

# Opciones por proveedor. Sólo se envía lo que Mixpost reconoce: las claves
# desconocidas se descartan en silencio del lado servidor.
DEFAULT_OPTIONS = {
    "youtube": {"status": "public", "made_for_kids": False},
    "instagram": {"type": "reel"},
    "instagram_standalone": {"type": "reel"},
    "facebook_page": {"type": "reel"},
    "tiktok": {
        "privacy_level": {"account-0": "PUBLIC_TO_EVERYONE"},
        "allow_comments": {"account-0": True},
        "allow_duet": {"account-0": False},
        "allow_stitch": {"account-0": False},
    },
}

_workspace_cache: str | None = None


def _configuration() -> tuple[str, str]:
    """Devuelve (base_url, api_key) de la API de Mixpost."""
    base_url = (
        os.environ.get("MIXPOST_BASE_URL")
        or os.environ.get("MIXPOST_API_URL")
        or ""
    ).rstrip("/")
    api_key = os.environ.get("MIXPOST_API_KEY", "").strip()
    if not base_url or not api_key:
        raise RuntimeError(
            "MIXPOST_BASE_URL y MIXPOST_API_KEY son obligatorios. "
            "La clave se crea en la pagina API del panel de Mixpost "
            "(no es la APP_KEY de Laravel)."
        )
    return base_url, api_key


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}


async def _workspace(client: httpx.AsyncClient, base_url: str, api_key: str) -> str:
    """Resuelve el UUID del workspace: el configurado o el primero del usuario."""
    global _workspace_cache
    configured = os.environ.get("MIXPOST_WORKSPACE_UUID", "").strip()
    if configured:
        return configured
    if _workspace_cache:
        return _workspace_cache

    response = await client.get(f"{base_url}/workspaces", headers=_headers(api_key))
    response.raise_for_status()
    payload = response.json()
    workspaces = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(workspaces, list) or not workspaces:
        raise RuntimeError("Mixpost no devolvio ningun workspace para este token")
    uuid = workspaces[0].get("uuid")
    if not isinstance(uuid, str) or not uuid:
        raise RuntimeError("El workspace de Mixpost no tiene un uuid utilizable")
    _workspace_cache = uuid
    return uuid


async def list_accounts() -> list[dict]:
    """Cuentas de Mixpost capaces de publicar vídeo."""
    base_url, api_key = _configuration()
    async with httpx.AsyncClient(timeout=30) as client:
        workspace = await _workspace(client, base_url, api_key)
        response = await client.get(
            f"{base_url}/{workspace}/accounts", headers=_headers(api_key)
        )
        response.raise_for_status()
        payload = response.json()

    accounts = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(accounts, list):
        raise RuntimeError("Mixpost devolvio una lista de cuentas invalida")

    result: list[dict] = []
    for account in accounts:
        if not isinstance(account, dict):
            continue
        provider = account.get("provider")
        account_id = account.get("id")
        if provider not in VIDEO_PROVIDERS or not isinstance(account_id, int):
            continue
        if not account.get("authorized", True):
            continue
        result.append(
            {
                "id": str(account_id),
                "provider": provider,
                "username": account.get("username"),
                "name": account.get("name"),
                "profile_url": account.get("profile_url"),
            }
        )
    return result


def _storage_video_url(video_url: str) -> None:
    """Valida que el vídeo venga del host público de nuestros medios."""
    public_base_url = os.environ.get("STORAGE_PUBLIC_BASE_URL", "")
    source = urlsplit(video_url)
    trusted = urlsplit(public_base_url)
    if (
        source.scheme != "https"
        or not source.hostname
        or source.username is not None
        or source.password is not None
        or source.port not in (None, 443)
        or trusted.scheme != "https"
        or not trusted.hostname
        or source.hostname.lower() != trusted.hostname.lower()
    ):
        raise ValueError(
            "video_url debe ser HTTPS y pertenecer al host de STORAGE_PUBLIC_BASE_URL"
        )


async def _upload_video(
    client: httpx.AsyncClient, base_url: str, api_key: str, workspace: str, video_url: str
) -> int:
    """Descarga el MP4 del almacenamiento publico y lo sube a la biblioteca de
    Mixpost. Devuelve el id numerico del medio."""
    chunks: list[bytes] = []
    size = 0
    async with client.stream("GET", video_url) as response:
        response.raise_for_status()
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > MAX_VIDEO_BYTES:
                raise ValueError(
                    f"El MP4 excede el limite de {MAX_VIDEO_BYTES} bytes de Mixpost"
                )
            chunks.append(chunk)
    video = b"".join(chunks)
    if len(video) < 12 or video[4:8] != b"ftyp":
        raise ValueError("video_url no contiene un archivo MP4 valido")

    response = await client.post(
        f"{base_url}/{workspace}/media",
        headers=_headers(api_key),
        files={"file": ("video.mp4", video, "video/mp4")},
    )
    response.raise_for_status()
    uploaded = response.json()
    if not isinstance(uploaded, dict) or "id" not in uploaded:
        raise RuntimeError("Mixpost devolvio datos de subida invalidos")
    try:
        return int(uploaded["id"])
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Mixpost devolvio un id de medio no numerico") from exc


def _schedule_fields(publish_at: datetime | None) -> dict:
    """Mixpost separa fecha, hora y zona horaria en campos propios."""
    if publish_at is None:
        return {"schedule_now": True}
    timezone_name = os.environ.get("MIXPOST_TIMEZONE", "UTC")
    if publish_at.tzinfo is not None:
        key = getattr(publish_at.tzinfo, "key", None)
        if key:
            timezone_name = key
    local = publish_at.astimezone(timezone.utc)
    return {
        "schedule": True,
        "date": local.strftime("%Y-%m-%d"),
        "time": local.strftime("%H:%M"),
        "timezone": timezone_name,
    }


def _provider_for(account: dict) -> str:
    provider = account.get("provider")
    if provider not in VIDEO_PROVIDERS:
        raise ValueError(
            f"La cuenta de Mixpost {account.get('id')} usa el proveedor {provider}, "
            "que no admite publicacion de video"
        )
    return provider


def _options_for(provider: str, title: str, custom: dict) -> dict:
    options = dict(DEFAULT_OPTIONS.get(provider, {}))
    if provider == "youtube":
        options["title"] = title[:256]
    if isinstance(custom, dict) and custom:
        options.update(custom)
    return {provider: options}


async def create_post(post: dict) -> dict:
    """Publica un vídeo en una cuenta de Mixpost."""
    base_url, api_key = _configuration()
    try:
        account_id = int(post["integration_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("integration_id debe ser el id numerico de una cuenta") from exc

    metadata = post.get("metadata") or {}
    custom_settings = metadata.get("settings", {})
    if not isinstance(custom_settings, dict):
        raise ValueError("metadata.settings debe ser un objeto")

    # Se valida el origen del video ANTES de cualquier peticion saliente:
    # una URL fuera del almacenamiento publico no debe generar trafico.
    _storage_video_url(str(post["video_url"]))

    async with httpx.AsyncClient(timeout=300) as client:
        workspace = await _workspace(client, base_url, api_key)

        accounts_response = await client.get(
            f"{base_url}/{workspace}/accounts", headers=_headers(api_key)
        )
        accounts_response.raise_for_status()
        accounts_payload = accounts_response.json()
        accounts = accounts_payload.get("data") if isinstance(accounts_payload, dict) else None
        if not isinstance(accounts, list):
            raise RuntimeError("Mixpost devolvio una lista de cuentas invalida")

        account = next(
            (
                item
                for item in accounts
                if isinstance(item, dict) and item.get("id") == account_id
            ),
            None,
        )
        if account is None:
            raise ValueError(f"La cuenta {account_id} no existe en Mixpost")
        provider = _provider_for(account)

        media_id = await _upload_video(
            client, base_url, api_key, workspace, str(post["video_url"])
        )

        title = post["title"]
        body = metadata.get("content", title)
        publish_at = post.get("publish_at")
        if isinstance(publish_at, str):
            publish_at = datetime.fromisoformat(publish_at)

        payload = {
            "accounts": [account_id],
            "tags": [],
            "versions": [
                {
                    "account_id": 0,
                    "is_original": True,
                    "content": [{"body": body, "media": [media_id]}],
                    "options": _options_for(provider, title, custom_settings),
                }
            ],
            **_schedule_fields(publish_at),
        }

        response = await client.post(
            f"{base_url}/{workspace}/posts",
            json=payload,
            headers={**_headers(api_key), "Content-Type": "application/json"},
        )
        response.raise_for_status()
        result = response.json()
    if not isinstance(result, dict):
        raise RuntimeError("Mixpost devolvio una respuesta con formato inesperado")
    return result
