"""Tests del adaptador de Open-Dispatch y de los endpoints del publisher.

El contrato verificado es el del codigo de Open-Dispatch
(https://github.com/Matthew-Selvam/Open-Dispatch, commit 773dd54):
  * POST /dispatch -> 202 {"unit_id", "enqueued"}
  * TikTok e Instagram leen `video_url` (URL publica)
  * YouTube lee `video_path` (ruta RELATIVA bajo OPEN_DISPATCH_MEDIA_DIR)
"""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

PUBLISHER_DIRECTORY = Path(__file__).parents[1] / "apps" / "publisher-service"
sys.path.insert(0, str(PUBLISHER_DIRECTORY))

from adapters import open_dispatch_adapter  # noqa: E402

MP4 = b"\x00\x00\x00\x18ftypisomfake-video"


def _mock_client(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    async_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        open_dispatch_adapter.httpx,
        "AsyncClient",
        lambda **kwargs: async_client(transport=transport, **kwargs),
    )


def _base_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPEN_DISPATCH_BASE_URL", "http://open-dispatch:8000")
    monkeypatch.setenv("OPEN_DISPATCH_API_TOKEN", "test-token")
    monkeypatch.setenv("STORAGE_PUBLIC_BASE_URL", "https://cdn.example")
    monkeypatch.setenv("OPEN_DISPATCH_MEDIA_DIR", str(tmp_path))


def _dispatch_handler(observed: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "open-dispatch":
            assert request.url.path == "/dispatch"
            assert request.headers["Authorization"] == "Bearer test-token"
            observed["payload"] = json.loads(request.content)
            return httpx.Response(
                202,
                json={
                    "unit_id": "unit-1",
                    "enqueued": [
                        {"id": "row-1", "target": "tiktok", "scheduled_for": "now"}
                    ],
                },
            )
        if request.url.host == "cdn.example":
            return httpx.Response(200, content=MP4)
        raise AssertionError(f"Peticion inesperada: {request.url}")

    return handler


def test_dispatch_posts_content_unit_with_three_platforms(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base_env(monkeypatch, tmp_path)
    observed: dict = {}
    _mock_client(monkeypatch, _dispatch_handler(observed))

    result = asyncio.run(
        open_dispatch_adapter.dispatch_content(
            {
                "video_url": "https://cdn.example/renders/video.mp4",
                "title": "Tema de prueba",
                "platforms": ["tiktok", "instagram", "youtube"],
                "publish_at": None,
                "metadata": {"content": "Descripcion de prueba"},
            }
        )
    )

    assert result["unit_id"] == "unit-1"
    payload = observed["payload"]
    assert payload["targets"] == ["tiktok", "instagram", "youtube"]
    assert payload["scheduled_for"] is None

    formats = payload["formats"]
    # TikTok: URL publica + caption + privacidad.
    assert formats["tiktok_post"]["video_url"] == (
        "https://cdn.example/renders/video.mp4"
    )
    assert formats["tiktok_post"]["caption"] == "Descripcion de prueba"
    assert formats["tiktok_post"]["privacy"] == "PUBLIC_TO_EVERYONE"
    # Instagram: REEL a partir de la URL publica.
    assert formats["instagram_post"]["video_url"] == (
        "https://cdn.example/renders/video.mp4"
    )
    assert formats["instagram_post"]["caption"] == "Descripcion de prueba"
    # YouTube: ruta relativa dentro del volumen de medios compartido.
    youtube = formats["youtube_short"]
    assert youtube["title"] == "Tema de prueba"
    assert youtube["description"] == "Descripcion de prueba"
    assert youtube["privacy"] == "public"
    video_path = youtube["video_path"]
    assert not video_path.startswith("/")
    assert ".." not in Path(video_path).parts
    assert (tmp_path / video_path).is_file()


def test_dispatch_stages_the_video_in_the_shared_media_volume(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base_env(monkeypatch, tmp_path)
    observed: dict = {}
    _mock_client(monkeypatch, _dispatch_handler(observed))

    asyncio.run(
        open_dispatch_adapter.dispatch_content(
            {
                "video_url": "https://cdn.example/renders/video.mp4",
                "title": "Short",
                "platforms": ["youtube"],
                "metadata": {},
            }
        )
    )

    video_path = observed["payload"]["formats"]["youtube_short"]["video_path"]
    staged = tmp_path / video_path
    assert staged.is_file()
    assert staged.read_bytes() == MP4
    # El nombre es estable: la misma URL produce el mismo fichero.
    assert staged.name.endswith(".mp4")


def test_dispatch_schedules_with_publish_at_in_utc(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base_env(monkeypatch, tmp_path)
    observed: dict = {}
    _mock_client(monkeypatch, _dispatch_handler(observed))

    asyncio.run(
        open_dispatch_adapter.dispatch_content(
            {
                "video_url": "https://cdn.example/renders/video.mp4",
                "title": "Programado",
                "platforms": ["tiktok"],
                "publish_at": "2026-11-05T15:30:00-04:00",
                "metadata": {},
            }
        )
    )

    assert observed["payload"]["scheduled_for"] == "2026-11-05T19:30:00+00:00"


def test_dispatch_without_youtube_does_not_download_the_video(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base_env(monkeypatch, tmp_path)
    observed: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "open-dispatch", (
            "Sin YouTube no hace falta descargar el MP4"
        )
        observed["payload"] = json.loads(request.content)
        return httpx.Response(202, json={"unit_id": "unit-2", "enqueued": []})

    _mock_client(monkeypatch, handler)

    asyncio.run(
        open_dispatch_adapter.dispatch_content(
            {
                "video_url": "https://cdn.example/renders/video.mp4",
                "title": "Solo TikTok",
                "platforms": ["tiktok"],
                "metadata": {},
            }
        )
    )

    formats = observed["payload"]["formats"]
    assert set(formats) == {"tiktok_post"}
    assert not list(tmp_path.rglob("*.mp4"))


def test_dispatch_accepts_platform_account_targets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base_env(monkeypatch, tmp_path)
    observed: dict = {}
    _mock_client(monkeypatch, _dispatch_handler(observed))

    asyncio.run(
        open_dispatch_adapter.dispatch_content(
            {
                "video_url": "https://cdn.example/renders/video.mp4",
                "title": "Con cuenta",
                "platforms": ["youtube:mi_canal", "ig"],
                "metadata": {},
            }
        )
    )

    assert observed["payload"]["targets"] == ["youtube:mi_canal", "instagram"]


def test_dispatch_rejects_video_outside_configured_storage_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base_env(monkeypatch, tmp_path)
    _mock_client(
        monkeypatch,
        lambda request: (_ for _ in ()).throw(
            AssertionError("No se debe descargar una URL fuera del almacenamiento")
        ),
    )

    with pytest.raises(ValueError, match="STORAGE_PUBLIC_BASE_URL"):
        asyncio.run(
            open_dispatch_adapter.dispatch_content(
                {
                    "video_url": "https://127.0.0.1/private.mp4",
                    "title": "No permitido",
                    "platforms": ["tiktok"],
                    "metadata": {},
                }
            )
        )


def test_dispatch_rejects_unknown_platform(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base_env(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="Plataforma no soportada"):
        asyncio.run(
            open_dispatch_adapter.dispatch_content(
                {
                    "video_url": "https://cdn.example/renders/video.mp4",
                    "title": "X",
                    "platforms": ["myspace"],
                    "metadata": {},
                }
            )
        )


def test_dispatch_requires_at_least_one_platform(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base_env(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="al menos una plataforma"):
        asyncio.run(
            open_dispatch_adapter.dispatch_content(
                {
                    "video_url": "https://cdn.example/renders/video.mp4",
                    "title": "X",
                    "platforms": [],
                    "metadata": {},
                }
            )
        )


def test_dispatch_truncates_caption_to_platform_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base_env(monkeypatch, tmp_path)
    observed: dict = {}
    _mock_client(monkeypatch, _dispatch_handler(observed))

    asyncio.run(
        open_dispatch_adapter.dispatch_content(
            {
                "video_url": "https://cdn.example/renders/video.mp4",
                "title": "Largo",
                "platforms": ["tiktok", "instagram"],
                "metadata": {"content": "x" * 3000},
            }
        )
    )

    formats = observed["payload"]["formats"]
    assert len(formats["tiktok_post"]["caption"]) == 2200
    assert len(formats["instagram_post"]["caption"]) == 2200


def test_dispatch_rejects_invalid_tiktok_privacy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base_env(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="privacy de TikTok invalida"):
        asyncio.run(
            open_dispatch_adapter.dispatch_content(
                {
                    "video_url": "https://cdn.example/renders/video.mp4",
                    "title": "X",
                    "platforms": ["tiktok"],
                    "metadata": {"settings": {"privacy": "secreto"}},
                }
            )
        )


def test_list_accounts_returns_only_configured_platforms(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "tiktok-token")
    monkeypatch.delenv("IG_USER_ID", raising=False)
    monkeypatch.delenv("IG_TOKEN", raising=False)
    monkeypatch.delenv("YOUTUBE_CLIENT_ID", raising=False)
    monkeypatch.delenv("YOUTUBE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("YOUTUBE_REFRESH_TOKEN", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/profiles"
        assert request.headers["Authorization"] == "Bearer test-token"
        return httpx.Response(200, json=[])

    _mock_client(monkeypatch, handler)

    accounts = asyncio.run(open_dispatch_adapter.list_accounts())
    assert [account["platform"] for account in accounts] == ["tiktok"]
    assert accounts[0]["name"] == "TikTok"


def test_list_accounts_includes_platforms_configured_in_profiles(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base_env(monkeypatch, tmp_path)
    for variable in (
        "TIKTOK_ACCESS_TOKEN",
        "IG_USER_ID",
        "IG_TOKEN",
        "YOUTUBE_CLIENT_ID",
        "YOUTUBE_CLIENT_SECRET",
        "YOUTUBE_REFRESH_TOKEN",
    ):
        monkeypatch.delenv(variable, raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=[{"id": "p1", "name": "Marca", "configured": ["instagram"]}]
        )

    _mock_client(monkeypatch, handler)

    accounts = asyncio.run(open_dispatch_adapter.list_accounts())
    assert [account["platform"] for account in accounts] == ["instagram"]


def test_configuration_requires_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPEN_DISPATCH_BASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="OPEN_DISPATCH_BASE_URL"):
        open_dispatch_adapter._configuration()


# ── Endpoints del publisher ────────────────────────────────────────────────


def _load_publisher_module():
    """Carga main.py del publisher con un nombre propio.

    ``apps/api/main.py`` y ``apps/publisher-service/main.py`` se llaman igual:
    importarlo como ``main`` chocaria con el modulo de la API ya importado
    por otros tests.
    """
    spec = importlib.util.spec_from_file_location(
        "publisher_main", PUBLISHER_DIRECTORY / "main.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["publisher_main"] = module
    spec.loader.exec_module(module)
    return module


def test_publisher_publishes_to_open_dispatch_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load_publisher_module()
    monkeypatch.delenv("PUBLISHER_BACKEND", raising=False)
    captured: dict = {}

    async def fake_dispatch(payload: dict) -> dict:
        captured["payload"] = payload
        return {"unit_id": "unit-9", "enqueued": []}

    monkeypatch.setattr(
        publisher.open_dispatch_adapter, "dispatch_content", fake_dispatch
    )

    client = TestClient(publisher.app)
    response = client.post(
        "/v1/publish",
        json={
            "video_url": "https://cdn.example/renders/video.mp4",
            "title": "Tema",
            "platforms": ["tiktok", "youtube"],
        },
    )

    assert response.status_code == 200
    assert response.json()["unit_id"] == "unit-9"
    assert captured["payload"]["platforms"] == ["tiktok", "youtube"]


def test_publisher_returns_422_for_unsupported_platform(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    publisher = _load_publisher_module()
    monkeypatch.delenv("PUBLISHER_BACKEND", raising=False)
    monkeypatch.setenv("OPEN_DISPATCH_BASE_URL", "http://open-dispatch:8000")
    monkeypatch.setenv("STORAGE_PUBLIC_BASE_URL", "https://cdn.example")
    monkeypatch.setenv("OPEN_DISPATCH_MEDIA_DIR", str(tmp_path))

    async_client = httpx.AsyncClient
    monkeypatch.setattr(
        publisher.open_dispatch_adapter.httpx,
        "AsyncClient",
        lambda **kwargs: async_client(
            transport=httpx.MockTransport(
                lambda request: (_ for _ in ()).throw(
                    AssertionError("No se debe despachar una plataforma invalida")
                )
            ),
            **kwargs,
        ),
    )

    client = TestClient(publisher.app)
    response = client.post(
        "/v1/publish",
        json={
            "video_url": "https://cdn.example/renders/video.mp4",
            "title": "Tema",
            "platforms": ["myspace"],
        },
    )

    # La validacion ocurre en el adaptador; el publisher la traduce a 422.
    assert response.status_code == 422




def test_publisher_surfaces_the_scheduler_detail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Open-Dispatch responde 400 con {"detail": {"errors": [...]}}.

    El publisher debe traducirlo a 502 incluyendo ese detalle: sin el, un
    rechazo por una ContentUnit invalida seria indescifrable.
    """
    publisher = _load_publisher_module()
    monkeypatch.delenv("PUBLISHER_BACKEND", raising=False)
    monkeypatch.setenv("OPEN_DISPATCH_BASE_URL", "http://open-dispatch:8000")
    monkeypatch.setenv("OPEN_DISPATCH_API_TOKEN", "test-token")
    monkeypatch.setenv("STORAGE_PUBLIC_BASE_URL", "https://cdn.example")
    monkeypatch.setenv("OPEN_DISPATCH_MEDIA_DIR", str(tmp_path))

    async_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/dispatch"
        return httpx.Response(
            400,
            json={"detail": {"errors": ["targets must be non-empty"]}},
        )

    monkeypatch.setattr(
        publisher.open_dispatch_adapter.httpx,
        "AsyncClient",
        lambda **kwargs: async_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )

    client = TestClient(publisher.app)
    response = client.post(
        "/v1/publish",
        json={
            "video_url": "https://cdn.example/renders/video.mp4",
            "title": "Tema",
            "platforms": ["tiktok"],
        },
    )

    assert response.status_code == 502
    assert "targets must be non-empty" in response.json()["detail"]


def test_publisher_healthz_reports_the_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load_publisher_module()
    monkeypatch.setenv("PUBLISHER_BACKEND", "open_dispatch")

    client = TestClient(publisher.app)
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "backend": "open_dispatch"}
