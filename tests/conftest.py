"""Configuracion minima para que la suite corra sin infraestructura externa.

`apps/api/main.py` instancia `Settings()` a nivel de modulo, asi que importar
la API exige que existan las variables obligatorias. Aqui se inyectan valores
validos pero ficticios antes de que se importe cualquier modulo de test.
"""

from __future__ import annotations

import os

# Solo se define lo que falte: si el entorno ya trae una configuracion real
# (por ejemplo en un job de CI con secretos), esta no se pisa.
DEFAULTS = {
    "DATABASE_URL": "postgresql://content:content@127.0.0.1:5432/content",
    "WORKER_API_TOKEN": "test-worker-token",
    "S3_ENDPOINT_URL": "https://test-account.r2.cloudflarestorage.com",
    "S3_BUCKET": "test-bucket",
    "S3_ACCESS_KEY_ID": "test-access-key",
    "S3_SECRET_ACCESS_KEY": "test-secret-key",
}

for _name, _value in DEFAULTS.items():
    os.environ.setdefault(_name, _value)
