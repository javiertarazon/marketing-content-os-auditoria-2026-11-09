"""Smoke test end-to-end del publisher contra un Open-Dispatch simulado.

Levanta dos servidores HTTP reales:

  1. Un scheduler falso que imita las rutas de Open-Dispatch que consume el
     publisher (``POST /dispatch``, ``GET /api/profiles``, ``GET /healthz``).
  2. El publisher (FastAPI) con uvicorn.

Y comprueba el flujo completo:

  * el publisher se autentica contra el scheduler con el token Bearer,
  * la ContentUnit que llega a Open-Dispatch tiene el formato que espera
    (``targets`` + ``formats`` con una clave por plataforma),
  * una URL de video que no pertenece al almacenamiento publico se rechaza
    SIN llegar a generar trafico hacia el scheduler,
  * ``GET /v1/integrations`` devuelve las plataformas publicables.

No necesita Docker, ni red, ni credenciales reales.

Uso::

    python scripts/smoke_publisher.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
PUBLISHER_DIRECTORY = ROOT / "apps" / "publisher-service"

API_TOKEN = "smoke-token"
VIDEO_URL = "https://cdn.example/renders/video.mp4"
STORAGE_PUBLIC_BASE_URL = "https://cdn.example"

received: list[dict] = []


class FakeSchedulerHandler(BaseHTTPRequestHandler):
    """Implementa el contrato de Open-Dispatch que usa el publisher."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:  # silencia el log del servidor
        return

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - firma de BaseHTTPRequestHandler
        if self.path == "/healthz":
            self._send(200, {"status": "ok", "version": "0.4.0"})
            return
        if self.path == "/api/profiles":
            self._send(200, [])
            return
        self._send(404, {"detail": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - firma de BaseHTTPRequestHandler
        if self.path != "/dispatch":
            self._send(404, {"detail": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8")
        received.append(
            {
                "authorization": self.headers.get("Authorization"),
                "unit": json.loads(raw),
            }
        )
        self._send(202, {"unit_id": "unit-smoke", "enqueued": [{"id": "row-1"}]})


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(
    base_url: str, path: str, *, method: str = "GET", payload: dict | None = None
) -> tuple[int, object]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(
        f"{base_url}{path}", data=body, headers=headers, method=method
    )
    try:
        with urlopen(request, timeout=20) as response:
            content = response.read()
            return response.status, json.loads(content) if content else None
    except HTTPError as error:
        content = error.read()
        try:
            return error.code, json.loads(content) if content else None
        except json.JSONDecodeError:
            return error.code, content.decode("utf-8", "replace")


def _load_publisher_module():
    # El publisher importa el paquete `adapters` de su propio directorio.
    if str(PUBLISHER_DIRECTORY) not in sys.path:
        sys.path.insert(0, str(PUBLISHER_DIRECTORY))
    spec = importlib.util.spec_from_file_location(
        "publisher_smoke_main", PUBLISHER_DIRECTORY / "main.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["publisher_smoke_main"] = module
    spec.loader.exec_module(module)
    return module


def _wait_for(url: str, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=2):
                return
        except Exception:  # noqa: BLE001 - el servidor aun no escucha
            time.sleep(0.1)
    raise TimeoutError(f"{url} no respondio en {timeout}s")


def main() -> int:
    import uvicorn

    scheduler_port = _free_port()
    publisher_port = _free_port()
    scheduler_base = f"http://127.0.0.1:{scheduler_port}"

    os.environ["PUBLISHER_BACKEND"] = "open_dispatch"
    os.environ["OPEN_DISPATCH_BASE_URL"] = scheduler_base
    os.environ["OPEN_DISPATCH_API_TOKEN"] = API_TOKEN
    os.environ["STORAGE_PUBLIC_BASE_URL"] = STORAGE_PUBLIC_BASE_URL
    os.environ["TIKTOK_ACCESS_TOKEN"] = "smoke-tiktok-token"
    os.environ.pop("IG_USER_ID", None)
    os.environ.pop("IG_TOKEN", None)
    os.environ.pop("YOUTUBE_CLIENT_ID", None)
    os.environ.pop("YOUTUBE_CLIENT_SECRET", None)
    os.environ.pop("YOUTUBE_REFRESH_TOKEN", None)

    media_dir = tempfile.mkdtemp(prefix="publisher-smoke-")
    os.environ["OPEN_DISPATCH_MEDIA_DIR"] = media_dir

    scheduler = ThreadingHTTPServer(("127.0.0.1", scheduler_port), FakeSchedulerHandler)
    threading.Thread(target=scheduler.serve_forever, daemon=True).start()

    publisher = _load_publisher_module()
    config = uvicorn.Config(
        publisher.app,
        host="127.0.0.1",
        port=publisher_port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()

    publisher_base = f"http://127.0.0.1:{publisher_port}"
    failures: list[str] = []

    try:
        _wait_for(f"{publisher_base}/healthz")
        _wait_for(f"{scheduler_base}/healthz")

        status, payload = _request(publisher_base, "/healthz")
        if status != 200 or payload != {"status": "ok", "backend": "open_dispatch"}:
            failures.append(f"/healthz devolvio {status} {payload}")

        status, payload = _request(
            publisher_base,
            "/v1/publish",
            method="POST",
            payload={
                "video_url": VIDEO_URL,
                "title": "Tema de prueba",
                "platforms": ["tiktok", "instagram"],
                "metadata": {"content": "Descripcion"},
            },
        )
        if status != 200 or payload.get("unit_id") != "unit-smoke":
            failures.append(f"/v1/publish devolvio {status} {payload}")

        if len(received) != 1:
            failures.append(f"el scheduler recibio {len(received)} dispatches (se esperaba 1)")
        else:
            call = received[0]
            if call["authorization"] != f"Bearer {API_TOKEN}":
                failures.append(f"cabecera Authorization incorrecta: {call['authorization']!r}")
            unit = call["unit"]
            if unit.get("targets") != ["tiktok", "instagram"]:
                failures.append(f"targets inesperados: {unit.get('targets')}")
            formats = unit.get("formats") or {}
            if set(formats) != {"tiktok_post", "instagram_post"}:
                failures.append(f"formats inesperados: {sorted(formats)}")
            if formats.get("tiktok_post", {}).get("video_url") != VIDEO_URL:
                failures.append("tiktok_post.video_url no coincide con la URL enviada")
            if formats.get("instagram_post", {}).get("caption") != "Descripcion":
                failures.append("instagram_post.caption no coincide con la descripcion")

        # Una URL fuera del almacenamiento publico se rechaza sin llamar al
        # scheduler: es el control que evita el uso abusivo del proxy.
        before = len(received)
        status, payload = _request(
            publisher_base,
            "/v1/publish",
            method="POST",
            payload={
                "video_url": "https://otro-host.example/video.mp4",
                "title": "No permitido",
                "platforms": ["tiktok"],
            },
        )
        if status != 422:
            failures.append(f"una URL ajena al almacenamiento devolvio {status} en vez de 422")
        if len(received) != before:
            failures.append("una URL ajena al almacenamiento genero trafico hacia el scheduler")

        status, payload = _request(
            publisher_base,
            "/v1/publish",
            method="POST",
            payload={
                "video_url": VIDEO_URL,
                "title": "No permitido",
                "platforms": ["myspace"],
            },
        )
        if status != 422:
            failures.append(f"una plataforma no soportada devolvio {status} en vez de 422")

        status, payload = _request(publisher_base, "/v1/integrations")
        if status != 200:
            failures.append(f"/v1/integrations devolvio {status}")
        else:
            platforms = [item.get("platform") for item in payload]
            if platforms != ["tiktok"]:
                failures.append(f"/v1/integrations devolvio {platforms} (se esperaba ['tiktok'])")
    finally:
        server.should_exit = True
        scheduler.shutdown()

    if failures:
        print("SMOKE FALLIDO:")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("SMOKE OK: publisher <-> Open-Dispatch (dispatch, autenticacion, guardas y /v1/integrations)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
