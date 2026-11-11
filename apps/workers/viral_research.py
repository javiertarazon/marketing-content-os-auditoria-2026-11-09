"""Busqueda de videos virales en YouTube para la etapa de investigacion.

Dos caminos,decidedos por la presencia de ``YOUTUBE_API_KEY``:

1. **Data API oficial** (preferido cuando hay clave): ``GET /youtube/v3/search`` con
   ``order=viewCount`` y luego ``GET /youtube/v3/videos`` para las metricas. Es el camino
   del notebook ``01_research_scrapegraph.ipynb`` (celda ``search_youtube_videos``).
2. **Extractor sin clave** (por defecto, porque ``YOUTUBE_API_KEY`` esta vacia en el
   ``.env`` local): descarga la pagina de resultados de YouTube con un User-Agent de
   navegador y extrae titulo, canal, vistas y URL con expresiones regulares sobre el
   HTML de resultados (claves ``ytInitialData``, ``"viewCount":{"simpleText":"..."}``,
   ``"shortViewCount"``). Es tolerante: si el formato de YouTube cambia devuelve lo que
   encuentre y lo reporta como ``partial`` en vez de reventar.

Ninguno de los dos caminos lanza excepcion hacia el pipeline: si la red falla o YouTube
bloquea la peticion se devuelve ``status="error"`` con el mensaje y el worker continua.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
YOUTUBE_RESULTS_URL = "https://www.youtube.com/results"
# Filtro de popularidad de YouTube: "ordenar por numero de vistas".
POPULARITY_FILTER = "CAM%3D"
DEFAULT_LIMIT = 6
REQUEST_TIMEOUT = (10, 30)
MAX_TEXT_CHARS = 1200
PLATFORM_DOMAINS: dict[str, tuple[str, ...]] = {
    "reddit": ("reddit.com",),
    "x": ("x.com", "twitter.com"),
    "instagram": ("instagram.com",),
    "tiktok": ("tiktok.com",),
    "facebook": ("facebook.com",),
    "youtube": ("youtube.com",),
}

_VIEW_COUNT_PATTERNS = (
    r'"viewCountText":\{"simpleText":"((?:[^"\\]|\\.)*)"',
    r'"viewCount":\{"simpleText":"((?:[^"\\]|\\.)*)"',
    r'"shortViewCount":\{"simpleText":"((?:[^"\\]|\\.)*)"',
    r'"viewCountText":\{"runs":\[\{"text":"((?:[^"\\]|\\.)*)"',
)
_TITLE_PATTERNS = (
    r'"title":\{"runs":\[\{"text":"((?:[^"\\]|\\.)*)"',
    r'"title":\{"simpleText":"((?:[^"\\]|\\.)*)"',
    r'"headline":\{"simpleText":"((?:[^"\\]|\\.)*)"',
)
_CHANNEL_PATTERNS = (
    r'"ownerText":\{"runs":\[\{"text":"((?:[^"\\]|\\.)*)"',
    r'"longBylineText":\{"runs":\[\{"text":"((?:[^"\\]|\\.)*)"',
    r'"shortBylineText":\{"runs":\[\{"text":"((?:[^"\\]|\\.)*)"',
    r'"channelName":"((?:[^"\\]|\\.)*)"',
)
_PUBLISHED_PATTERN = r'"publishedTimeText":\{"simpleText":"((?:[^"\\]|\\.)*)"'
_VIDEO_ID_PATTERN = re.compile(r'"videoId":"([A-Za-z0-9_-]{11})"')
_WINDOW_CHARS = 9000


def _matches_platform(url: str, platform: str) -> bool:
    """Indica si una URL pertenece a los dominios publicos de la plataforma."""
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    allowed = {
        candidate
        for domain in PLATFORM_DOMAINS.get(platform, ())
        for candidate in (domain, f"www.{domain}", f"m.{domain}")
    }
    return host in allowed


def _unescape(value: str) -> str:
    """Convierte una cadena escapada de JSON en texto plano."""
    try:
        return json.loads(f'"{value}"')
    except (ValueError, TypeError):
        return value


def _first_match(patterns: Iterable[str], window: str) -> str | None:
    """Devuelve el primer grupo capturado de los patrones que casen con la ventana."""
    for pattern in patterns:
        match = re.search(pattern, window)
        if match:
            text = _unescape(match.group(1)).strip()
            if text:
                return text
    return None


def parse_view_count(text: str | None) -> int | None:
    """Convierte un texto de vistas ("1.234.567 de vistas", "12K views") en un entero."""
    if not text:
        return None
    normalized = text.replace("\u00a0", " ").strip()
    multiplier = 1
    suffix_match = re.search(r"([\d.,\s]+)\s*([KMB])\b", normalized, flags=re.IGNORECASE)
    if suffix_match:
        multiplier = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[
            suffix_match.group(2).upper()
        ]
        digits = suffix_match.group(1)
    else:
        digits_match = re.search(r"(\d[\d.,\s]*)", normalized)
        if not digits_match:
            return None
        digits = digits_match.group(1)
    cleaned = re.sub(r"[^\d]", "", digits)
    if not cleaned:
        return None
    try:
        return int(cleaned) * multiplier
    except ValueError:
        return None


def _parse_relative_time(text: str | None) -> str | None:
    """Convierte "hace 3 meses" / "3 months ago" en una marca ISO aproximada."""
    if not text:
        return None
    lowered = text.lower()
    units = {
        "segundo": "seconds",
        "minuto": "minutes",
        "hora": "hours",
        "día": "days",
        "dia": "days",
        "semana": "weeks",
        "mes": "months",
        "año": "days",
        "ano": "days",
    }
    for spanish, english in units.items():
        match = re.search(rf"(\d+)\s*{spanish}", lowered)
        if not match:
            continue
        amount = int(match.group(1))
        if english == "days" and "año" in lowered or english == "days" and "ano" in lowered:
            delta = timedelta(days=365 * amount)
        elif english == "days":
            delta = timedelta(days=amount)
        else:
            delta = timedelta(**{english: amount})
        return (datetime.now(timezone.utc) - delta).isoformat().replace("+00:00", "Z")
    return None


def _source(
    title: str,
    video_id: str,
    channel: str | None,
    views: int | None,
    published_at: str | None,
) -> dict[str, Any]:
    """Construye la entrada de fuente con la forma que espera el contrato de salida."""
    return {
        "platform": "youtube",
        "source_type": "video_metrics",
        "title": title,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "view_count": views,
        "views_per_hour": None,
        "published_at": published_at,
        "channel": channel or "",
    }


def scrape_youtube_results(
    topic: str, limit: int = DEFAULT_LIMIT
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Extrae videos de la pagina de resultados de YouTube sin usar ninguna API key.

    Devuelve ``(sources, status)``. Nunca lanza: los fallos de red o de formato se
    traducen en ``status="error"`` o ``status="partial"``.
    """
    params = {"search_query": topic, "sp": POPULARITY_FILTER}
    headers = {
        "User-Agent": BROWSER_USER_AGENT,
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        response = requests.get(
            YOUTUBE_RESULTS_URL, params=params, headers=headers, timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
    except requests.RequestException as error:
        message = f"No se pudo consultar YouTube: {error.__class__.__name__}: {error}"[:240]
        logger.warning(message)
        return [], {
            "platform": "youtube",
            "status": "error",
            "result_count": 0,
            "message": message,
        }

    html = response.text
    sources = _extract_sources_from_html(html)
    if not sources:
        return [], {
            "platform": "youtube",
            "status": "no_results",
            "result_count": 0,
            "message": (
                "YouTube no devolvio resultados legibles para el tema; "
                "puede haber bloqueado la peticion anonima."
            ),
        }

    ordered = sorted(
        sources,
        key=lambda item: item.get("view_count") if item.get("view_count") else -1,
        reverse=True,
    )[: max(1, limit)]
    with_views = [item for item in ordered if item.get("view_count")]
    status = "ok" if len(with_views) == len(ordered) else "partial"
    message = (
        f"{len(ordered)} videos ordenados por vistas."
        if status == "ok"
        else f"{len(with_views)} de {len(ordered)} videos traen el numero de vistas."
    )
    return ordered, {
        "platform": "youtube",
        "status": status,
        "result_count": len(ordered),
        "message": message,
    }


def _extract_sources_from_html(html: str) -> list[dict[str, Any]]:
    """Extrae las fuentes del HTML de resultados con expresiones regulares."""
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in _VIDEO_ID_PATTERN.finditer(html):
        video_id = match.group(1)
        if video_id in seen:
            continue
        window = html[match.end() : match.end() + _WINDOW_CHARS]
        title = _first_match(_TITLE_PATTERNS, window)
        if not title:
            continue
        seen.add(video_id)
        views_text = _first_match(_VIEW_COUNT_PATTERNS, window)
        channel = _first_match(_CHANNEL_PATTERNS, window)
        published_at = _parse_relative_time(_first_match((_PUBLISHED_PATTERN,), window))
        sources.append(
            _source(title, video_id, channel, parse_view_count(views_text), published_at)
        )
    return sources


def search_youtube_videos_api(
    topic: str,
    limit: int = DEFAULT_LIMIT,
    region_code: str = "VE",
    api_key: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Busca videos con la Data API oficial de YouTube (``order=viewCount``)."""
    key = api_key or os.environ.get("YOUTUBE_API_KEY") or ""
    if not key:
        return [], {
            "platform": "youtube",
            "status": "error",
            "result_count": 0,
            "message": "YOUTUBE_API_KEY no esta definida.",
        }
    now = datetime.now(timezone.utc)
    published_after = (now - timedelta(days=14)).isoformat().replace("+00:00", "Z")
    try:
        search = requests.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "part": "snippet",
                "type": "video",
                "order": "viewCount",
                "q": topic,
                "maxResults": max(1, limit),
                "regionCode": region_code,
                "publishedAfter": published_after,
                "key": key,
            },
            timeout=REQUEST_TIMEOUT,
        )
        search.raise_for_status()
        ids = [
            item.get("id", {}).get("videoId")
            for item in search.json().get("items", [])
            if item.get("id", {}).get("videoId")
        ]
        if not ids:
            return [], {
                "platform": "youtube",
                "status": "no_results",
                "result_count": 0,
                "message": "La API no encontro videos coincidentes de los ultimos 14 dias.",
            }
        details = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"part": "snippet,statistics", "id": ",".join(ids), "key": key},
            timeout=REQUEST_TIMEOUT,
        )
        details.raise_for_status()
    except requests.RequestException as error:
        # Nunca se imprime la excepcion completa: la URL puede contener la clave.
        message = f"La Data API de YouTube fallo: {error.__class__.__name__}"[:240]
        logger.warning(message)
        return [], {
            "platform": "youtube",
            "status": "error",
            "result_count": 0,
            "message": message,
        }

    sources: list[dict[str, Any]] = []
    for item in details.json().get("items", []):
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        published_at = snippet.get("publishedAt")
        try:
            published = datetime.fromisoformat(str(published_at).replace("Z", "+00:00"))
            age_hours = max((now - published).total_seconds() / 3600, 1 / 60)
            views = int(stats.get("viewCount", 0))
        except (AttributeError, TypeError, ValueError):
            continue
        sources.append(
            _source(
                snippet.get("title", ""),
                str(item.get("id", "")),
                snippet.get("channelTitle", ""),
                views,
                published_at,
            )
        )
        sources[-1]["views_per_hour"] = round(views / age_hours, 2)
    sources.sort(key=lambda item: item.get("views_per_hour") or 0, reverse=True)
    return sources[: max(1, limit)], {
        "platform": "youtube",
        "status": "metrics_available",
        "result_count": len(sources),
        "message": (
            "Ordenado por vistas por hora entre resultados coincidentes recientes; "
            "no representa un ranking global de viralidad."
        ),
    }


def search_viral_videos(
    topic: str,
    limit: int = DEFAULT_LIMIT,
    region_code: str | None = None,
    api_key: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Punto de entrada: usa la Data API si hay clave y el extractor sin clave si no.

    Devuelve ``(sources, platform_status)``. Si la Data API falla con clave presente se
    degrada al extractor sin clave; si ambos caminos fallan se devuelve el estado de
    error para que el worker siga adelante.
    """
    resolved_key = api_key if api_key is not None else os.environ.get("YOUTUBE_API_KEY")
    if resolved_key:
        sources, status = search_youtube_videos_api(
            topic,
            limit=limit,
            region_code=(region_code or os.environ.get("YOUTUBE_REGION_CODE") or "VE").upper(),
            api_key=resolved_key,
        )
        if sources:
            return sources, status
        logger.warning(
            "La Data API de YouTube no devolvio resultados; se usa el extractor sin clave."
        )
    return scrape_youtube_results(topic, limit=limit)


def search_platform_snippets(
    topic: str, platform: str, limit: int = 2
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Busca fragmentos publicos de una plataforma con DuckDuckGo (sin API key).

    Solo devuelve los titulos y un resumen del indice: no hay metricas de visualizaciones.
    Es el equivalente al camino ``snippet_only`` del notebook.
    """
    domains = PLATFORM_DOMAINS.get(platform)
    if not domains:
        return [], {
            "platform": platform,
            "status": "error",
            "result_count": 0,
            "message": f"Plataforma de investigacion no admitida: {platform}",
        }
    query = f"site:{domains[0]} {topic}"
    try:
        from ddgs import DDGS

        snippets = list(DDGS(timeout=10).text(query, max_results=max(1, limit)))
    except Exception as error:  # noqa: BLE001 - DuckDuckGo puede fallar o limitarse
        message = f"DuckDuckGo no respondio: {type(error).__name__}: {error}"[:240]
        logger.warning(message)
        return [], {
            "platform": platform,
            "status": "error",
            "result_count": 0,
            "message": message,
        }

    sources: list[dict[str, Any]] = []
    for item in snippets:
        url = item.get("href", "")
        body = (item.get("body") or "").strip()
        if not url or not _matches_platform(url, platform) or len(body) < 40:
            continue
        sources.append(
            {
                "platform": platform,
                "source_type": "search_snippet",
                "title": (item.get("title") or "").strip(),
                "url": url,
                "view_count": None,
                "published_at": None,
                "channel": "",
                "text": body[:MAX_TEXT_CHARS],
            }
        )
        if len(sources) >= limit:
            break

    if not sources:
        return [], {
            "platform": platform,
            "status": "no_results",
            "result_count": 0,
            "message": "DuckDuckGo no devolvio resultados publicos para esta plataforma.",
        }
    return sources, {
        "platform": platform,
        "status": "snippet_only",
        "result_count": len(sources),
        "message": "Se usan solo fragmentos del indice; no se pudo leer el post completo.",
    }


def main() -> int:
    """Prueba manual: ``python -m apps.workers.viral_research "finanzas personales"``."""
    import argparse

    parser = argparse.ArgumentParser(description="Busca videos virales de YouTube sin API key.")
    parser.add_argument("--topic", required=True, help="Tema a buscar.")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Numero de videos.")
    parser.add_argument("--region-code", default=None, help="Codigo de region para la Data API.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sources, status = search_viral_videos(
        args.topic, limit=args.limit, region_code=args.region_code
    )
    logger.info("Estado de la plataforma: %s", status)
    for index, item in enumerate(sources, start=1):
        logger.info(
            "%d. %s | %s vistas | canal: %s | %s",
            index,
            item["title"],
            item.get("view_count"),
            item.get("channel") or "desconocido",
            item.get("url"),
        )
    return 0 if sources else 1


if __name__ == "__main__":
    raise SystemExit(main())
