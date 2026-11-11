"""Adaptador de Open-Dispatch (MIT) para publicar vídeo en TikTok, Instagram y YouTube.

Open-Dispatch expone una unica API JSON:

    POST /dispatch             -> 202 {"unit_id": ..., "enqueued": [...]}
    GET  /queue                -> estado de la cola
    GET  /campaign/{unit_id}   -> filas de una campana
    POST /campaign/{unit_id}/cancel
    GET  /healthz              -> {"status": "ok", "version": ...}

Autenticacion: ``Authorization: Bearer $OPEN_DISPATCH_API_TOKEN`` en todas las
rutas salvo ``/healthz`` (si el token esta vacio, Open-Dispatch no exige
autenticacion).

Un dispatch es una *ContentUnit*:

    {
      "targets": ["tiktok", "instagram", "youtube"],
      "scheduled_for": "2026-11-05T15:30:00+00:00",
      "formats": {
        "tiktok_post":    {"video_url": ..., "caption": ..., "privacy": ...},
        "instagram_post": {"caption": ..., "video_url": ...},
        "youtube_short":  {"video_path": "renders/x.mp4", "title": ..., ...}
      }
    }

Cada red consume una clave de formato distinta y el worker entrega la MISMA
unidad a cada adaptador, asi que un dispatch a varias redes lleva varias claves
a la vez.

Dos reglas del contrato condicionan el diseno de este adaptador:

* TikTok e Instagram descargan el video de una URL publica. Se usa la URL
  publica del bucket de medios (``STORAGE_PUBLIC_BASE_URL``).
* YouTube sube el archivo desde disco: ``video_path`` debe ser una ruta
  RELATIVA, sin ``..``, que resuelva bajo ``OPEN_DISPATCH_MEDIA_DIR``. Por eso
  el publisher descarga el MP4 a un volumen compartido con Open-Dispatch.

Contrato verificado contra el codigo de Open-Dispatch
(https://github.com/Matthew-Selvam/Open-Dispatch, commit 773dd54), no contra
documentacion de terceros.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

# Plataformas que publica este servicio.
SUPPORTED_PLATFORMS: tuple[str, ...] = ("tiktok", "instagram", "youtube")

# Clave de formato que lee el adaptador de cada plataforma.
FORMAT_KEYS: dict[str, str] = {
    "tiktok": "tiktok_post",
    "instagram": "instagram_post",
    "youtube": "youtube_short",
}

# Credenciales minimas por plataforma. Mismo criterio que
# ``open-dispatch/api/app.py::_platforms_configured``: una plataforma esta
# configurada cuando estan puestas todas sus variables.
PLATFORM_CREDENTIALS: dict[str, tuple[str, ...]] = {
    "tiktok": ("TIKTOK_ACCESS_TOKEN",),
    "instagram": ("IG_USER_ID", "IG_TOKEN"),
    "youtube": (
        "YOUTUBE_CLIENT_ID",
        "YOUTUBE_CLIENT_SECRET",
        "YOUTUBE_REFRESH_TOKEN",
    ),
}

PLATFORM_LABELS: dict[str, str] = {
    "tiktok": "TikTok",
    "instagram": "Instagram",
    "youtube": "YouTube",
}

# Alias admitidos en la peticion, normalizados al nombre de la plataforma.
PLATFORM_ALIASES: dict[str, str] = {
    "tiktok": "tiktok",
    "instagram": "instagram",
    "ig": "instagram",
    "reels": "instagram",
    "instagram_reels": "instagram",
    "youtube": "youtube",
    "yt": "youtube",
    "shorts": "youtube",
    "youtube_short": "youtube",
}

# Niveles de privacidad de TikTok (Content Posting API v2).
TIKTOK_PRIVACY_LEVELS = {"PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"}

# Visibilidades aceptadas por YouTube.
YOUTUBE_PRIVACY_LEVELS = {"public", "unlisted", "private"}

# Limites de texto (los mismos que usa Open-Dispatch).
CAPTION_LIMITS = {"tiktok": 2200, "instagram": 2200, "youtube": 5000}

# Limite por defecto del MP4 descargado al volumen compartido.
DEFAULT_MAX_VIDEO_BYTES = 250_000_000

# Subcarpeta del volumen de medios donde se dejan los renders.
MEDIA_SUBDIR = "renders"

# Reintentos de conexion al dispatch: en Docker el publisher puede arrancar
# antes de que Open-Dispatch este listo.
DISPATCH_ATTEMPTS = 3
DISPATCH_BACKOFF_SECONDS = 1.0


def _max_video_bytes() -> int:
    raw = os.environ.get("OPEN_DISPATCH_MAX_VIDEO_BYTES", "").strip()
    if not raw:
        return DEFAULT_MAX_VIDEO_BYTES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_VIDEO_BYTES
    return value if value > 0 else DEFAULT_MAX_VIDEO_BYTES


def _configuration() -> tuple[str, str]:
    """Devuelve (base_url, api_token) de Open-Dispatch."""
    base_url = os.environ.get("OPEN_DISPATCH_BASE_URL", "").strip().rstrip("/")
    token = os.environ.get("OPEN_DISPATCH_API_TOKEN", "").strip()
    if not base_url:
        raise RuntimeError(
            "OPEN_DISPATCH_BASE_URL es obligatorio "
            "(por ejemplo http://open-dispatch:8000 dentro de Docker)."
        )
    return base_url, token


def _headers(token: str) -> dict[str, str]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def media_root() -> Path:
    """Raiz del volumen de medios compartido con Open-Dispatch.

    Debe coincidir con ``OPEN_DISPATCH_MEDIA_DIR`` del contenedor de
    Open-Dispatch: su worker resuelve ``video_path`` bajo esa misma raiz.
    """
    configured = os.environ.get("OPEN_DISPATCH_MEDIA_DIR")
    if configured is not None:
        return Path(configured).expanduser().resolve()
    data = os.environ.get("OPEN_DISPATCH_DATA", "data").strip() or "data"
    return Path(data).expanduser().resolve() / "media"


def _validate_public_video_url(video_url: str) -> None:
    """Valida que el vídeo venga del host público de nuestros medios."""
    public_base_url = os.environ.get("STORAGE_PUBLIC_BASE_URL", "").strip()
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


def normalize_platform(value: str) -> str:
    """Normaliza un nombre de plataforma a su clave canonica."""
    key = value.strip().lower()
    platform = PLATFORM_ALIASES.get(key)
    if platform is None:
        raise ValueError(
            f"Plataforma no soportada: {value!r}. Soportadas: "
            + ", ".join(SUPPORTED_PLATFORMS)
        )
    return platform


def _parse_target(value: str) -> tuple[str, str | None]:
    """Interpreta un destino ``plataforma`` o ``plataforma:cuenta``."""
    raw = value.strip().lower()
    if not raw:
        raise ValueError("El destino no puede estar vacio")
    platform_raw, separator, account = raw.partition(":")
    platform = normalize_platform(platform_raw)
    if separator:
        if not re.fullmatch(r"[a-z0-9._-]+", account):
            raise ValueError(f"Cuenta invalida en el destino {value!r}")
    else:
        account = None
    return platform, account


def _media_filename(video_url: str) -> str:
    """Nombre estable para el MP4 descargado (misma URL, mismo fichero)."""
    parsed = urlsplit(video_url)
    stem = Path(parsed.path).stem or "video"
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", stem)[:60]
    digest = hashlib.sha256(video_url.encode("utf-8")).hexdigest()[:12]
    return f"{stem}-{digest}.mp4"


async def _stage_video(client: httpx.AsyncClient, video_url: str) -> str:
    """Descarga el MP4 al volumen compartido y devuelve su ruta RELATIVA.

    Open-Dispatch rechaza las rutas absolutas y las que contienen ``..``: el
    worker resuelve ``video_path`` bajo ``OPEN_DISPATCH_MEDIA_DIR``.
    """
    root = media_root()
    target = root / MEDIA_SUBDIR / _media_filename(video_url)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    limit = _max_video_bytes()
    size = 0
    header = b""
    try:
        async with client.stream("GET", video_url) as response:
            response.raise_for_status()
            with partial.open("wb") as handle:
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > limit:
                        raise ValueError(
                            f"El MP4 excede el limite de {limit} bytes de Open-Dispatch"
                        )
                    if len(header) < 12:
                        header += chunk[: 12 - len(header)]
                    handle.write(chunk)
        if len(header) < 12 or header[4:8] != b"ftyp":
            raise ValueError("video_url no contiene un archivo MP4 valido")
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    partial.replace(target)
    return f"{MEDIA_SUBDIR}/{target.name}"


def _caption(metadata: dict, title: str) -> str:
    content = metadata.get("content")
    return str(content) if content else title


def _settings(metadata: dict) -> dict:
    settings = metadata.get("settings") or {}
    if not isinstance(settings, dict):
        raise ValueError("metadata.settings debe ser un objeto")
    return settings


def _build_formats(
    platforms: list[str],
    video_url: str,
    title: str,
    metadata: dict,
    video_path: str | None,
) -> dict:
    """Construye el bloque ``formats`` de la ContentUnit."""
    settings = _settings(metadata)
    caption = _caption(metadata, title)
    formats: dict[str, dict] = {}

    if "tiktok" in platforms:
        privacy = str(
            settings.get("privacy")
            or os.environ.get("TIKTOK_PRIVACY_LEVEL")
            or "PUBLIC_TO_EVERYONE"
        ).strip().upper()
        if privacy not in TIKTOK_PRIVACY_LEVELS:
            raise ValueError(
                f"privacy de TikTok invalida: {privacy!r}. Valores: "
                + ", ".join(sorted(TIKTOK_PRIVACY_LEVELS))
            )
        formats[FORMAT_KEYS["tiktok"]] = {
            "video_url": video_url,
            "caption": caption[: CAPTION_LIMITS["tiktok"]],
            "privacy": privacy,
        }

    if "instagram" in platforms:
        formats[FORMAT_KEYS["instagram"]] = {
            "caption": caption[: CAPTION_LIMITS["instagram"]],
            "video_url": video_url,
        }

    if "youtube" in platforms:
        if not video_path:
            raise ValueError(
                "No se pudo preparar el MP4 local para YouTube (video_path vacio)"
            )
        privacy = str(settings.get("youtube_privacy") or "public").strip().lower()
        if privacy not in YOUTUBE_PRIVACY_LEVELS:
            raise ValueError(
                f"youtube_privacy invalida: {privacy!r}. Valores: "
                + ", ".join(sorted(YOUTUBE_PRIVACY_LEVELS))
            )
        tags = settings.get("tags") or []
        if not isinstance(tags, (list, tuple)):
            raise ValueError("metadata.settings.tags debe ser una lista")
        formats[FORMAT_KEYS["youtube"]] = {
            "video_path": video_path,
            "title": title[:100],
            "description": caption[: CAPTION_LIMITS["youtube"]],
            "tags": [str(tag) for tag in tags][:30],
            "privacy": privacy,
        }

    return formats


def _scheduled_for(publish_at: object) -> str | None:
    """Normaliza ``publish_at`` al ISO-8601 UTC que espera Open-Dispatch."""
    if publish_at is None or publish_at == "":
        return None
    if isinstance(publish_at, str):
        try:
            publish_at = datetime.fromisoformat(publish_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"publish_at no es una fecha ISO-8601 valida: {publish_at!r}") from exc
    if not isinstance(publish_at, datetime):
        raise ValueError("publish_at debe ser una fecha ISO-8601")
    if publish_at.tzinfo is None:
        raise ValueError("publish_at debe incluir zona horaria")
    return publish_at.astimezone(timezone.utc).isoformat()


def _targets(raw_platforms: object) -> tuple[list[str], list[str], dict[str, str | None]]:
    """Normaliza la lista de plataformas a (plataformas, targets, cuentas)."""
    if isinstance(raw_platforms, str):
        raw_platforms = [raw_platforms]
    if not isinstance(raw_platforms, (list, tuple)) or not raw_platforms:
        raise ValueError(
            "Hay que indicar al menos una plataforma en 'platforms' ("
            + ", ".join(SUPPORTED_PLATFORMS)
            + ")"
        )

    platforms: list[str] = []
    targets: list[str] = []
    accounts: dict[str, str | None] = {}
    for item in raw_platforms:
        platform, account = _parse_target(str(item))
        if platform not in platforms:
            platforms.append(platform)
            accounts[platform] = account
        target = f"{platform}:{account}" if account else platform
        if target not in targets:
            targets.append(target)
    return platforms, targets, accounts


async def dispatch_content(payload: dict) -> dict:
    """Encola la publicacion en Open-Dispatch y devuelve su respuesta."""
    base_url, token = _configuration()

    if not isinstance(payload, dict):
        raise ValueError("El payload de publicacion debe ser un objeto")

    video_url = str(payload.get("video_url") or "").strip()
    if not video_url:
        raise ValueError("video_url es obligatorio")
    # La validacion del origen ocurre ANTES de cualquier peticion saliente:
    # una URL fuera del almacenamiento publico no debe generar trafico.
    _validate_public_video_url(video_url)

    title = str(payload.get("title") or "").strip()
    if not title:
        raise ValueError("title es obligatorio")

    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ValueError("metadata debe ser un objeto")

    platforms, targets, _accounts = _targets(payload.get("platforms"))
    scheduled_for = _scheduled_for(payload.get("publish_at"))

    video_path: str | None = None
    async with httpx.AsyncClient(timeout=300) as client:
        if "youtube" in platforms:
            # YouTube sube desde disco: hace falta una copia local en el
            # volumen compartido con Open-Dispatch.
            video_path = await _stage_video(client, video_url)

        formats = _build_formats(platforms, video_url, title, metadata, video_path)
        unit = {
            "category": str(payload.get("category") or "marketing"),
            "targets": targets,
            "scheduled_for": scheduled_for,
            "formats": formats,
        }

        response = await _post_dispatch(client, base_url, token, unit)

    result = response.json()
    if not isinstance(result, dict):
        raise RuntimeError(
            "Open-Dispatch devolvio una respuesta con formato inesperado"
        )
    return result


async def _post_dispatch(
    client: httpx.AsyncClient, base_url: str, token: str, unit: dict
) -> httpx.Response:
    """POST /dispatch con reintentos de conexion (el arranque puede ser lento)."""
    last_error: Exception | None = None
    for attempt in range(DISPATCH_ATTEMPTS):
        try:
            response = await client.post(
                f"{base_url}/dispatch", json=unit, headers=_headers(token)
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            last_error = exc
            if attempt < DISPATCH_ATTEMPTS - 1:
                await asyncio.sleep(DISPATCH_BACKOFF_SECONDS * (attempt + 1))
                continue
            raise
        response.raise_for_status()
        return response
    raise RuntimeError(f"No se pudo encolar la publicacion: {last_error}")


def _platform_configured(platform: str) -> bool:
    return all(
        os.environ.get(variable, "").strip()
        for variable in PLATFORM_CREDENTIALS[platform]
    )


async def _profile_platforms(base_url: str, token: str) -> set[str]:
    """Plataformas configuradas en perfiles de Open-Dispatch (best effort).

    Open-Dispatch no expone un endpoint JSON con los canales configurados por
    variable de entorno (eso solo aparece en el panel HTML); los perfiles si
    tienen endpoint JSON en ``/api/profiles``.
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{base_url}/api/profiles", headers=_headers(token)
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError):
        return set()

    platforms: set[str] = set()
    if isinstance(payload, list):
        for profile in payload:
            if not isinstance(profile, dict):
                continue
            for name in profile.get("configured") or []:
                try:
                    platforms.add(normalize_platform(str(name)))
                except ValueError:
                    continue
    return platforms


async def list_accounts() -> list[dict]:
    """Plataformas publicables con la configuracion actual.

    Se combinan dos fuentes:

    * Las variables de entorno (mismo fichero ``.env`` que consume
      Open-Dispatch), que son las que el worker usa para publicar.
    * Los perfiles de Open-Dispatch, que pueden traer credenciales extra.
    """
    base_url, token = _configuration()
    configured = {
        platform for platform in SUPPORTED_PLATFORMS if _platform_configured(platform)
    }
    configured |= await _profile_platforms(base_url, token)

    accounts: list[dict] = []
    for platform in SUPPORTED_PLATFORMS:
        if platform not in configured:
            continue
        accounts.append(
            {
                "id": platform,
                "platform": platform,
                "provider": platform,
                "name": PLATFORM_LABELS[platform],
                "configured": True,
                "requires": list(PLATFORM_CREDENTIALS[platform]),
            }
        )
    return accounts
