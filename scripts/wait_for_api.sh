#!/usr/bin/env bash
# Espera a que la API responda en /healthz antes de generar los tipos.
#
# El target 'dev' del Makefile encadenaba 'docker compose up -d' (asincrono) con
# 'generate-types', que necesita el esquema OpenAPI de una API ya viva: en el
# primer 'make dev' la generacion fallaba porque la API todavia no escuchaba.
#
# Uso: bash scripts/wait_for_api.sh [URL] [TIMEOUT_SEGUNDOS]

set -euo pipefail

API_URL=${1:-${API_BASE_URL:-http://localhost:8000}}
TIMEOUT=${2:-120}
INTERVAL=2

echo "Esperando a la API en ${API_URL}/healthz (max ${TIMEOUT} s)..."

deadline=$(( $(date +%s) + TIMEOUT ))
while [ "$(date +%s)" -lt "$deadline" ]; do
    if curl -fsS "${API_URL}/healthz" >/dev/null 2>&1; then
        echo "API lista en ${API_URL}"
        exit 0
    fi
    sleep "$INTERVAL"
done

echo "ERROR: la API no respondio en ${API_URL}/healthz tras ${TIMEOUT} s." >&2
echo "       Revisa los logs: docker compose -f docker-compose.dev.yml logs api" >&2
exit 1
