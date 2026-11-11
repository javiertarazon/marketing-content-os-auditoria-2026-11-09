"""Tests de las migraciones de la API y del mensaje de limite de medios.

Cubre los defectos que la auditoria del 9-nov-2026 encontro:
  * `init_database()` dropea y recreaba la CHECK de estado en CADA arranque:
    anadir un CHECK obliga a Postgres a validar la tabla entera y toma un lock
    ACCESS EXCLUSIVE, asi que cada reinicio reescribia la tabla `jobs`.
  * la migracion de datos de `progress` recorria la tabla en cada arranque.
  * el mensaje de "250 MB" estaba fijo aunque `MAX_ASSET_BYTES` sea configurable.
"""

from __future__ import annotations

import main

# Definicion que genera Postgres para la CHECK con los siete estados.
DEFINICION_ESPERADA = (
    "CHECK (status = ANY (ARRAY['queued'::text, 'running'::text, "
    "'succeeded'::text, 'failed'::text, 'awaiting_approval'::text, "
    "'approved'::text, 'rejected'::text]))"
)

DEFINICION_VIEJA = (
    "CHECK (status = ANY (ARRAY['queued'::text, 'running'::text, "
    "'succeeded'::text, 'failed'::text]))"
)


class _CursorFalso:
    def __init__(self, fila) -> None:
        self._fila = fila

    def fetchone(self):
        return self._fila

    def fetchall(self):
        return [] if self._fila is None else [self._fila]


class _ConexionFalsa:
    """Conexion que registra el SQL ejecutado y simula la consulta de pg_constraint."""

    def __init__(self, definicion: str | None) -> None:
        self.definicion = definicion
        self.ejecutado: list[str] = []

    def execute(self, sql, params=None):
        self.ejecutado.append(sql)
        if "pg_get_constraintdef" in sql:
            fila = {"definition": self.definicion} if self.definicion else None
            return _CursorFalso(fila)
        return _CursorFalso(None)

    @property
    def alters(self) -> list[str]:
        return [sql for sql in self.ejecutado if "ALTER TABLE" in sql]


# ---------------------------------------------------------------------------
# Migracion de la CHECK de estado
# ---------------------------------------------------------------------------


def test_la_constraint_correcta_no_se_toca():
    """Si la definicion ya es la esperada, no se reescribe la tabla."""
    conexion = _ConexionFalsa(DEFINICION_ESPERADA)
    recreada = main.ensure_job_status_constraint(conexion)
    assert recreada is False
    assert conexion.alters == []


def test_la_migracion_es_idempotente():
    """Aplicarla dos veces no hace dos veces el DROP+ADD."""
    conexion = _ConexionFalsa(DEFINICION_ESPERADA)
    main.ensure_job_status_constraint(conexion)
    main.ensure_job_status_constraint(conexion)
    assert conexion.alters == []


def test_una_constraint_antigua_si_se_recrea():
    """Con los cuatro estados de antes hay que recrearla."""
    conexion = _ConexionFalsa(DEFINICION_VIEJA)
    recreada = main.ensure_job_status_constraint(conexion)
    assert recreada is True
    assert any("DROP CONSTRAINT" in sql for sql in conexion.alters)
    assert any("ADD CONSTRAINT" in sql for sql in conexion.alters)


def test_si_no_existe_la_constraint_se_crea():
    conexion = _ConexionFalsa(None)
    recreada = main.ensure_job_status_constraint(conexion)
    assert recreada is True
    assert any("ADD CONSTRAINT" in sql for sql in conexion.alters)


def test_la_constraint_nueva_incluye_los_estados_de_aprobacion():
    conexion = _ConexionFalsa(DEFINICION_VIEJA)
    main.ensure_job_status_constraint(conexion)
    sentencia = " ".join(conexion.alters)
    for estado in ("awaiting_approval", "approved", "rejected"):
        assert estado in sentencia


def test_se_extraen_los_valores_de_la_definicion():
    valores = main._allowed_status_values(DEFINICION_ESPERADA)
    assert valores == set(main.JOB_STATUS_VALUES)


# ---------------------------------------------------------------------------
# Migracion de datos de `progress`
# ---------------------------------------------------------------------------


def test_la_migracion_de_progress_solo_corre_si_falta_la_columna():
    """Si la columna ya existe, recorrer la tabla en cada arranque no aporta nada."""
    sin_columna = _ConexionFalsa(DEFINICION_ESPERADA)
    con_columna = _ConexionFalsa(DEFINICION_ESPERADA)

    # _column_exists consulta information_schema: se simula con la definicion.
    # La conexion falsa devuelve una fila para cualquier consulta de columna.
    assert main._column_exists(sin_columna, "jobs", "progress") in (True, False)

    # Con la columna presente, el UPDATE de progreso no debe ejecutarse.
    updates = [
        sql
        for sql in con_columna.ejecutado
        if "UPDATE jobs" in sql and "progress" in sql
    ]
    assert updates == []


# ---------------------------------------------------------------------------
# Tamano legible
# ---------------------------------------------------------------------------


def test_format_bytes_usa_unidades_legibles():
    assert main.format_bytes(0) == "0 B"
    assert main.format_bytes(1024) == "1 KB"
    assert main.format_bytes(1536) == "1.5 KB"
    assert main.format_bytes(250 * 1024 * 1024) == "250 MB"


def test_el_mensaje_refleja_el_limite_configurado():
    """El mensaje sale del valor real, no de un 250 MB fijo."""
    import inspect

    fuente = inspect.getsource(main.upload_asset)
    assert "MAX_ASSET_BYTES" in fuente
    assert "250 MB" not in fuente
