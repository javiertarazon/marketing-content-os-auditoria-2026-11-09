import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(
    0, str(Path(__file__).parents[1] / "apps" / "publisher-service")
)
from adapters import mixpost_adapter


def _mock_client(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    async_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        mixpost_adapter.httpx,
        "AsyncClient",
        lambda **kwargs: async_client(transport=transport, **kwargs),
    )
    monkeypatch.setattr(mixpost_adapter, "_workspace_cache", None)


def test_create_post_uploads_video_and_uses_mixpost_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIXPOST_BASE_URL", "https://mixpost.example/mixpost/api")
    monkeypatch.setenv("MIXPOST_API_KEY", "test-mixpost-key")
    monkeypatch.setenv("STORAGE_PUBLIC_BASE_URL", "https://cdn.example")
    mp4 = b"\x00\x00\x00\x18ftypisomfake-video"
    observed: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "mixpost.example":
            assert request.headers["Authorization"] == "Bearer test-mixpost-key"
        else:
            assert "Authorization" not in request.headers

        if request.url.path == "/mixpost/api/workspaces":
            return httpx.Response(
                200, json={"data": [{"id": 1, "uuid": "ws-uuid", "name": "Acme"}]}
            )
        if request.url.path.endswith("/accounts"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": 6,
                            "provider": "youtube",
                            "username": "canal",
                            "name": "Canal",
                            "authorized": True,
                        }
                    ]
                },
            )
        if request.url.path.endswith("/media"):
            assert b'filename="video.mp4"' in request.content
            assert mp4 in request.content
            return httpx.Response(200, json={"id": "7", "url": "https://mixpost.example/7.mp4"})
        if request.url.path.endswith("/posts"):
            observed["payload"] = json.loads(request.content)
            return httpx.Response(200, json={"id": 12, "uuid": "post-uuid"})
        if request.url.path.endswith(".mp4"):
            return httpx.Response(200, content=mp4)
        raise AssertionError(f"Unexpected Mixpost request: {request.url}")

    _mock_client(monkeypatch, handler)

    result = asyncio.run(
        mixpost_adapter.create_post(
            {
                "video_url": "https://cdn.example/renders/video.mp4",
                "title": "Tema de prueba",
                "platform": "youtube",
                "integration_id": "6",
                "publish_at": None,
                "metadata": {"content": "Descripción de prueba"},
            }
        )
    )

    assert result == {"id": 12, "uuid": "post-uuid"}
    payload = observed["payload"]
    # Contrato real de Mixpost: accounts + versions, no "posts"/"integration".
    assert payload["accounts"] == [6]
    assert payload["schedule_now"] is True
    version = payload["versions"][0]
    assert version["account_id"] == 0
    assert version["is_original"] is True
    assert version["content"][0]["body"] == "Descripción de prueba"
    # El medio se referencia por id numerico, no por objeto {id, path}.
    assert version["content"][0]["media"] == [7]
    assert version["options"]["youtube"]["title"] == "Tema de prueba"
    assert version["options"]["youtube"]["status"] == "public"


def test_create_post_schedules_with_date_time_and_timezone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIXPOST_BASE_URL", "https://mixpost.example/mixpost/api")
    monkeypatch.setenv("MIXPOST_API_KEY", "test-mixpost-key")
    monkeypatch.setenv("MIXPOST_WORKSPACE_UUID", "ws-uuid")
    monkeypatch.setenv("MIXPOST_TIMEZONE", "America/Caracas")
    monkeypatch.setenv("STORAGE_PUBLIC_BASE_URL", "https://cdn.example")
    mp4 = b"\x00\x00\x00\x18ftypisomfake-video"
    observed: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/accounts"):
            return httpx.Response(
                200,
                json={"data": [{"id": 3, "provider": "facebook_page", "authorized": True}]},
            )
        if request.url.path.endswith("/media"):
            return httpx.Response(200, json={"id": "9"})
        if request.url.path.endswith("/posts"):
            observed["payload"] = json.loads(request.content)
            return httpx.Response(200, json={"id": 13})
        if request.url.path.endswith(".mp4"):
            return httpx.Response(200, content=mp4)
        raise AssertionError(f"Unexpected Mixpost request: {request.url}")

    _mock_client(monkeypatch, handler)

    asyncio.run(
        mixpost_adapter.create_post(
            {
                "video_url": "https://cdn.example/renders/video.mp4",
                "title": "Programado",
                "platform": "facebook",
                "integration_id": "3",
                "publish_at": "2026-11-04T15:30:00+00:00",
                "metadata": {},
            }
        )
    )

    payload = observed["payload"]
    assert payload["schedule"] is True
    assert payload["date"] == "2026-11-04"
    assert payload["time"] == "15:30"
    assert payload["timezone"] == "America/Caracas"
    assert payload["versions"][0]["options"]["facebook_page"]["type"] == "reel"


def test_list_accounts_only_returns_video_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIXPOST_BASE_URL", "https://mixpost.example/mixpost/api")
    monkeypatch.setenv("MIXPOST_API_KEY", "test-mixpost-key")
    monkeypatch.setenv("MIXPOST_WORKSPACE_UUID", "ws-uuid")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/mixpost/api/ws-uuid/accounts"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": 1, "provider": "youtube", "username": "a", "authorized": True},
                    {"id": 2, "provider": "twitter", "username": "b", "authorized": True},
                    {"id": 3, "provider": "tiktok", "username": "c", "authorized": False},
                    {"id": 4, "provider": "instagram_standalone", "username": "d", "authorized": True},
                ]
            },
        )

    _mock_client(monkeypatch, handler)

    accounts = asyncio.run(mixpost_adapter.list_accounts())
    assert [account["id"] for account in accounts] == ["1", "4"]


def test_create_post_rejects_video_outside_configured_storage_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIXPOST_BASE_URL", "https://mixpost.example/mixpost/api")
    monkeypatch.setenv("MIXPOST_API_KEY", "test-mixpost-key")
    monkeypatch.setenv("STORAGE_PUBLIC_BASE_URL", "https://cdn.example")
    _mock_client(
        monkeypatch,
        lambda request: (_ for _ in ()).throw(
            AssertionError("No se debe descargar una URL fuera del almacenamiento")
        ),
    )

    with pytest.raises(ValueError, match="STORAGE_PUBLIC_BASE_URL"):
        asyncio.run(
            mixpost_adapter.create_post(
                {
                    "video_url": "https://127.0.0.1/private.mp4",
                    "title": "No permitido",
                    "platform": "youtube",
                    "integration_id": "6",
                    "metadata": {},
                }
            )
        )


def test_configuration_requires_base_url_and_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MIXPOST_BASE_URL", raising=False)
    monkeypatch.delenv("MIXPOST_API_URL", raising=False)
    monkeypatch.delenv("MIXPOST_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="MIXPOST_API_KEY"):
        mixpost_adapter._configuration()
