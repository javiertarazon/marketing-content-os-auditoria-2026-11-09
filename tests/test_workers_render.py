"""Tests del worker de render: validacion de la URL de audio y procesos hijos.

Cubre los defectos que la auditoria del 9-nov-2026 encontro:
  * `download_audio` hacia un GET a la URL del trabajo sin validar esquema ni
    host: era una puerta de SSRF dormida.
  * el timeout de `subprocess.run` mataba solo al proceso directo, dejaba vivos
    a los nietos (node, Chrome) y perdiase el MP4 ya renderizado; ademas el
    fallback de navegador no se activaba ante un timeout.
"""

from __future__ import annotations

import sys

import pytest

from apps.workers import local_render_worker as render

FALLO_FFMPEG = render.FFMPEG if hasattr(render, "FFMPEG") else "ffmpeg"


# ---------------------------------------------------------------------------
# Validacion de la URL de audio (defensa anti-SSRF)
# ---------------------------------------------------------------------------


def test_una_url_de_https_de_la_allowlist_se_acepta():
    url = "https://cdn.example.com/video.mp4"
    assert render.validate_audio_url(url, allowed_hosts={"cdn.example.com"}) == url


def test_un_host_local_se_acepta_sin_allowlist():
    url = "http://localhost:9000/video.mp4"
    assert render.validate_audio_url(url, allowed_hosts=set()) == url


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://cdn.example.com/video.mp4",
        "javascript:alert(1)",
        "",
    ],
)
def test_un_esquema_no_permitido_se_rechaza(url):
    with pytest.raises(ValueError):
        render.validate_audio_url(url, allowed_hosts={"cdn.example.com"})


def test_un_host_fuera_de_la_allowlist_se_rechaza():
    with pytest.raises(ValueError) as exc:
        render.validate_audio_url(
            "https://otro-sitio.example/video.mp4", allowed_hosts={"cdn.example.com"}
        )
    assert "allowlist" in str(exc.value)


def test_http_plano_fuera_de_localhost_se_rechaza():
    """Sin TLS las credenciales travels en claro: solo se admite en local."""
    with pytest.raises(ValueError) as exc:
        render.validate_audio_url(
            "http://cdn.example.com/video.mp4", allowed_hosts={"cdn.example.com"}
        )
    assert "https" in str(exc.value)


def test_un_host_sufijado_del_permitido_no_cuela():
    """cdn.example.com.otro.test no es cdn.example.com."""
    with pytest.raises(ValueError):
        render.validate_audio_url(
            "https://cdn.example.com.otro.test/video.mp4",
            allowed_hosts={"cdn.example.com"},
        )


def test_la_allowlist_se_toma_del_entorno(monkeypatch):
    monkeypatch.setenv("STORAGE_PUBLIC_BASE_URL", "https://media.example.com")
    monkeypatch.setenv("R2_PUBLIC_HOSTS", "otro.example.com,tercero.example.com")
    hosts = render.audio_allowed_hosts()
    assert "media.example.com" in hosts
    assert "otro.example.com" in hosts
    assert "tercero.example.com" in hosts


def test_download_audio_rechaza_una_url_no_permitida(tmp_path, monkeypatch):
    """La validacion ocurre ANTES de descargar nada."""
    destino = tmp_path / "voice.wav"
    llamadas = []

    class _Storage:
        def download_file(self, *args, **kwargs):  # pragma: no cover - no debe llegar
            raise AssertionError("no se debe descargar nada")

    monkeypatch.setattr(
        render.requests, "get", lambda *a, **k: llamadas.append(a) or None
    )
    with pytest.raises(ValueError):
        render.download_audio(
            {"audio_url": "https://no-permitido.example/video.mp4"},
            destino,
            _Storage(),
            "bucket",
        )
    assert llamadas == []


# ---------------------------------------------------------------------------
# Ejecucion de comandos externos
# ---------------------------------------------------------------------------


def test_un_comando_que_termina_con_error_lanza_runtime_error(tmp_path):
    with pytest.raises(RuntimeError):
        render.run_command(
            [sys.executable, "-c", "import sys; sys.exit(3)"], cwd=tmp_path, timeout=30
        )


def test_un_timeout_lanza_runtime_error_y_mata_el_grupo(tmp_path):
    """El timeout tiene que ser RuntimeError: asi se activa el fallback de navegador."""
    with pytest.raises(RuntimeError) as exc:
        render.run_command(
            [sys.executable, "-c", "import time; time.sleep(60)"], cwd=tmp_path, timeout=2
        )
    assert "limite" in str(exc.value).lower()


def test_un_comando_correcto_devuelve_su_salida(tmp_path):
    salida = render.run_command(
        [sys.executable, "-c", "print('listo')"], cwd=tmp_path, timeout=30
    )
    assert "listo" in salida


def test_el_comando_se_lanza_en_su_propio_grupo_de_procesos():
    """Sin grupo propio no se pueden matar los nietos (node, Chrome)."""
    opciones = render._process_group_options()
    if sys.platform == "win32":
        assert "creationflags" in opciones
    else:
        assert opciones.get("start_new_session") is True
