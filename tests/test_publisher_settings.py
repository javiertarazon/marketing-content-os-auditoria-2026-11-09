"""Tests de la configuracion del publisher y del diagnostico de la descarga.

Cubre los defectos que la auditoria del 9-nov-2026 encontro:
  * el fallo al descargar el MP4 para YouTube se reportaba como "no se pudo
    conectar con el scheduler", senalando al servicio equivocado.
  * la configuracion se leia con `os.environ.get(...)` sueltos, sin validacion,
    mientras la API usa `pydantic-settings`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

PUBLISHER_DIRECTORY = Path(__file__).parents[1] / "apps" / "publisher-service"
sys.path.insert(0, str(PUBLISHER_DIRECTORY))

import publisher_settings  # noqa: E402
from adapters import open_dispatch_adapter  # noqa: E402
from publisher_settings import PublisherSettings, get_settings  # noqa: E402

MP4 = b"\x00\x00\x00\x18ftypisomfake-video"


# ---------------------------------------------------------------------------
# Configuracion
# ---------------------------------------------------------------------------


def test_el_backend_por_defecto_es_open_dispatch(monkeypatch):
    monkeypatch.delenv("PUBLISHER_BACKEND", raising=False)
    assert get_settings().PUBLISHER_BACKEND == "open_dispatch"


def test_un_backend_invalido_se_rechaza(monkeypatch):
    monkeypatch.setenv("PUBLISHER_BACKEND", "inventado")
    with pytest.raises(RuntimeError) as exc:
        get_settings()
    assert "open_dispatch" in str(exc.value)


def test_el_backend_se_normaliza(monkeypatch):
    monkeypatch.setenv("PUBLISHER_BACKEND", "  MIXPOST  ")
    assert get_settings().PUBLISHER_BACKEND == "mixpost"


def test_los_cors_incluyen_el_puerto_del_dashboard(monkeypatch):
    """El dashboard corre en el 3000: si no esta, el navegador lo bloquea."""
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    origenes = publisher_settings.cors_origins()
    assert "http://localhost:3000" in origenes
    assert "http://localhost:3001" in origenes


def test_los_cors_se_pueden_configurar(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://mi-panel.test, http://otro.test")
    assert publisher_settings.cors_origins() == [
        "https://mi-panel.test",
        "http://otro.test",
    ]


def test_las_claves_desconocidas_no_abortan_el_arranque(monkeypatch):
    """El .env es compartido por varios consumidores."""
    monkeypatch.setenv("POSTGRES_USER", "content")
    assert PublisherSettings().PUBLISHER_BACKEND == "open_dispatch"


# ---------------------------------------------------------------------------
# Diagnostico de la descarga del video
# ---------------------------------------------------------------------------


def test_el_error_de_descarga_del_video_tiene_mensaje_propio():
    error = open_dispatch_adapter.VideoStageError(
        "https://cdn.example.com/video.mp4", httpx.ConnectError("no resuelve")
    )
    assert "descargar" in str(error).lower()
    assert "cdn.example.com" in str(error)
    assert isinstance(error, RuntimeError)


def test_el_publisher_no_reporta_el_fallo_de_descarga_como_problema_del_scheduler(
    monkeypatch,
):
    """El mensaje tiene que senalar al video, no al scheduler."""
    import importlib.util

    # El main del publisher no se importa como 'main': ese nombre ya lo usa
    # la API en la misma sesion de pytest.
    spec = importlib.util.spec_from_file_location(
        "publisher_main", PUBLISHER_DIRECTORY / "main.py"
    )
    publisher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publisher)

    async def _falla(client, video_url):
        raise httpx.ConnectError("no resuelve el host")

    monkeypatch.setattr(open_dispatch_adapter, "_stage_video", _falla)
    monkeypatch.setenv("OPEN_DISPATCH_BASE_URL", "http://scheduler.test:8000")
    monkeypatch.setenv("STORAGE_PUBLIC_BASE_URL", "https://cdn.example.com")
    monkeypatch.setenv("PUBLISHER_BACKEND", "open_dispatch")

    cliente = TestClient(publisher.app, raise_server_exceptions=False)
    respuesta = cliente.post(
        "/v1/publish",
        json={
            "video_url": "https://cdn.example.com/video.mp4",
            "title": "prueba",
            "platforms": ["youtube"],
        },
    )
    assert respuesta.status_code == 502
    detalle = respuesta.json()["detail"].lower()
    assert "descargar" in detalle or "video" in detalle
    assert "no se pudo conectar con el scheduler" not in detalle
