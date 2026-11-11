#!/usr/bin/env bash
# Generate TypeScript client types from FastAPI OpenAPI schema using openapi-typescript
# Requires openapi-typescript installed globally or in the project (npm i -g openapi-typescript)

set -euo pipefail

# Ensure the API is running locally (default http://localhost:8000).
# If not, you can start it with: .venv/bin/uvicorn apps.api.main:app --reload

API_URL=${API_URL:-http://localhost:8000}
OUTPUT_DIR="apps/dashboard/types"
OUTPUT_FILE="openapi.d.ts"

# Create output directory if missing
mkdir -p "$OUTPUT_DIR"

# Fetch the OpenAPI JSON spec.
# -f para que un 404 o un 500 fallen en vez de guardar el cuerpo del error como
# si fuera un esquema: sin eso el fallo aparece luego como error de parseo de
# openapi-typescript y esconde la causa real (que la API no esta levantada).
if ! curl -fsS "$API_URL/openapi.json" -o "$OUTPUT_DIR/openapi.json"; then
    echo "ERROR: no se pudo obtener el esquema OpenAPI de $API_URL/openapi.json" >&2
    echo "       Comprueba que la API esta levantada en $API_URL" >&2
    exit 1
fi

# Generate TypeScript declarations.
# openapi-typescript es una devDependency del dashboard con la version fijada:
# 'npx' sin version descarga la ultima publicada en cada ejecucion.
cd apps/dashboard
npx --no-install openapi-typescript "../$OUTPUT_DIR/openapi.json" --output "../$OUTPUT_DIR/$OUTPUT_FILE"
cd - >/dev/null

echo "✅ Generated TypeScript types at $OUTPUT_DIR/$OUTPUT_FILE"
