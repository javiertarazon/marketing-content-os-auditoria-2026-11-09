"""Tests del lease del trabajo y del bucle del worker.

Cubre los defectos que la auditoria del 9-nov-2026 encontro por ejecucion:
  * un solo fallo transitorio de heartbeat declaraba el lease perdido para
    siempre y tiraba hasta 30 minutos de trabajo ya invertido.
  * el bucle de reserva reintentaba para siempre sin tope, sin backoff y sin
    distinguir un 401 (configuracion) de un 5xx (transitorio), y salia con
    codigo 0: un wrapper veia "exito".
"""

from __future__ import annotations

import requests

from apps.workers import worker_common
from apps.workers.worker_common import (
    LeaseRenewer,
    WorkerAuthError,
    WorkerUnavailableError,
    run_worker_loop,
)


class _FalsaRespuesta:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def _error_http(status_code: int) -> requests.HTTPError:
    return requests.HTTPError(
        f"HTTP {status_code}", response=_FalsaRespuesta(status_code)
    )


class _ClienteFalso:
    """Cliente de la API con los fallos que le indiquemos."""

    def __init__(self, *, fallo_heartbeat=None, fallo_reserva=None, trabajo=None) -> None:
        self.fallo_heartbeat = fallo_heartbeat
        self.fallo_reserva = fallo_reserva
        self.trabajo = trabajo
        self.llamadas_heartbeat = 0

    def job_heartbeat(self, job_id, lease_token):
        self.llamadas_heartbeat += 1
        if self.fallo_heartbeat is not None:
            raise self.fallo_heartbeat
        return {"status": "ok"}

    def worker_heartbeat(self, job_id, stage, stopped=False):
        if self.fallo_heartbeat is not None:
            raise self.fallo_heartbeat
        return {"status": "ok"}

    def next_job(self):
        if self.fallo_reserva is not None:
            raise self.fallo_reserva
        return self.trabajo


# ---------------------------------------------------------------------------
# Renovacion del lease
# ---------------------------------------------------------------------------


def test_un_fallo_transitorio_no_tira_el_lease():
    """Con un fallo de red, el lease sigue vivo: el trabajo no se pierde."""
    cliente = _ClienteFalso(fallo_heartbeat=requests.RequestException("red caida"))
    renewer = LeaseRenewer(cliente, "job-1", "lease-1", max_failures=3)

    assert renewer.renew_once() is True
    assert renewer.lost is False
    renewer.ensure_active()  # no debe lanzar


def test_varios_fallos_consecutivos_si_tiran_el_lease():
    cliente = _ClienteFalso(fallo_heartbeat=requests.RequestException("red caida"))
    renewer = LeaseRenewer(cliente, "job-1", "lease-1", max_failures=3)

    assert renewer.renew_once() is True
    assert renewer.renew_once() is True
    assert renewer.renew_once() is False
    assert renewer.lost is True


def test_un_409_pierde_el_lease_de_inmediato():
    """La API responde 409 cuando ya no reconoce la reserva: no hay margen."""
    cliente = _ClienteFalso(fallo_heartbeat=_error_http(409))
    renewer = LeaseRenewer(cliente, "job-1", "lease-1", max_failures=5)

    assert renewer.renew_once() is False
    assert renewer.lost is True


def test_ensure_active_lanza_cuando_el_lease_se_perdio():
    cliente = _ClienteFalso(fallo_heartbeat=_error_http(409))
    renewer = LeaseRenewer(cliente, "job-1", "lease-1")
    renewer.renew_once()

    try:
        renewer.ensure_active()
    except RuntimeError as exc:
        assert "lease" in str(exc).lower()
    else:  # pragma: no cover - el test falla si no lanza
        raise AssertionError("ensure_active deberia lanzar con el lease perdido")


def test_una_renovacion_correcta_reinicia_el_contador_de_fallos():
    cliente = _ClienteFalso()
    renewer = LeaseRenewer(cliente, "job-1", "lease-1", max_failures=2)

    cliente.fallo_heartbeat = requests.RequestException("red caida")
    assert renewer.renew_once() is True
    cliente.fallo_heartbeat = None
    assert renewer.renew_once() is True
    assert renewer.consecutive_failures == 0


# ---------------------------------------------------------------------------
# Bucle de reserva de trabajo
# ---------------------------------------------------------------------------


def test_el_bucle_se_detiene_con_error_de_configuracion_si_la_api_rechaza_el_token():
    """Un token invalido (401) es un error de configuracion: no se reintenta."""
    cliente = _ClienteFalso(fallo_reserva=_error_http(401))

    try:
        run_worker_loop(cliente, lambda job: True, max_retries=5, backoff_seconds=0)
    except WorkerAuthError as exc:
        assert "WORKER_API_TOKEN" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("se esperaba WorkerAuthError")


def test_el_bloop_agota_los_reintentos_y_se_detiene():
    """Con la API caida se para con un error claro, no en un bucle infinito."""
    cliente = _ClienteFalso(fallo_reserva=requests.RequestException("sin conexion"))

    try:
        run_worker_loop(cliente, lambda job: True, max_retries=3, backoff_seconds=0)
    except WorkerUnavailableError as exc:
        assert "3 intentos" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("se esperaba WorkerUnavailableError")


def test_el_bucle_procesa_un_trabajo_y_devuelve_el_total():
    trabajo = {"job_id": "job-1", "lease_token": "lease-1"}
    cliente = _ClienteFalso(trabajo=trabajo)
    procesados = []

    total = run_worker_loop(
        cliente,
        lambda job: procesados.append(job) or True,
        max_jobs=1,
        backoff_seconds=0,
    )

    assert total == 1
    assert len(procesados) == 1


def test_el_bucle_no_escribe_secretos_en_el_log(caplog):
    """El token del worker no debe aparecer en los logs."""
    cliente = _ClienteFalso(fallo_reserva=_error_http(401))
    with caplog.at_level("DEBUG"):
        try:
            run_worker_loop(cliente, lambda job: True, max_retries=1, backoff_seconds=0)
        except WorkerAuthError:
            pass
    assert worker_common.WORKER_API_TOKEN not in caplog.text if hasattr(worker_common, "WORKER_API_TOKEN") else True
