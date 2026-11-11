"""Regresion: el arranque de la API no puede depender del layout del repo.

El bug original: ``env_file=Path(__file__).parents[2] / ".env"`` funciona en el
repositorio (apps/api/settings.py -> la raiz es parents[2]) pero lanza
``IndexError`` en la imagen de Docker (/app/settings.py solo tiene dos
``parents``), y el contenedor de la API moria al arrancar con el codigo 1.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_DIRECTORY = ROOT / "apps" / "api"


def _import_settings():
    sys.path.insert(0, str(API_DIRECTORY))
    sys.path.insert(0, str(ROOT / "packages" / "python"))
    import settings as settings_module  # noqa: PLC0415

    return settings_module


def test_resolve_env_file_returns_a_path() -> None:
    settings_module = _import_settings()
    resolved = settings_module.resolve_env_file()
    assert isinstance(resolved, Path)


def test_resolve_env_file_survives_the_docker_layout() -> None:
    """Simula /app/settings.py: sin parents[2] no debe haber IndexError."""
    settings_module = _import_settings()

    # Un fichero a un nivel superficial: parents[2] no existe.
    shallow = Path(tempfile.gettempdir()) / "mcos_layout_probe" / "app"
    shallow.mkdir(parents=True, exist_ok=True)
    probe = shallow / "probe_settings.py"
    probe.write_text(
        "from pathlib import Path\n"
        "\n"
        "\n"
        "def resolve() -> Path:\n"
        "    here = Path(__file__).resolve()\n"
        "    if len(here.parents) >= 3:\n"
        "        candidate = here.parents[2] / '.env'\n"
        "        if candidate.is_file():\n"
        "            return candidate\n"
        "    return here.parent / '.env'\n",
        encoding="utf-8",
    )
    code = "import probe_settings; print(probe_settings.resolve())"
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(shallow),
    )
    assert result.returncode == 0, result.stderr
    assert "IndexError" not in result.stderr
    assert settings_module.resolve_env_file is not None


def test_settings_module_imports_from_a_shallow_path() -> None:
    """Copia settings.py a un nivel superficial y comprueba que no revienta."""
    import shutil
    import tempfile

    settings_module = _import_settings()
    with tempfile.TemporaryDirectory() as tmp:
        # Estructura equivalente a /app/settings.py: sin directorio padre extra.
        shallow = Path(tmp) / "app"
        shallow.mkdir()
        target = shallow / "settings.py"
        shutil.copy(API_DIRECTORY / "settings.py", target)

        code = (
            "import sys; sys.path.insert(0, %r); import settings; "
            "print(settings.resolve_env_file())" % str(shallow)
        )
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        assert "IndexError" not in result.stderr
        assert settings_module.resolve_env_file is not None


def test_api_container_import_works_without_env_file(tmp_path) -> None:
    """La imagen no lleva .env: el import debe funcionar sin el fichero."""
    code = (
        "import sys; sys.path.insert(0, %r); sys.path.insert(0, %r); "
        "import settings; print(settings.Settings.model_fields.keys().__len__())"
        % (str(API_DIRECTORY), str(ROOT / "packages" / "python"))
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "IndexError" not in result.stderr
