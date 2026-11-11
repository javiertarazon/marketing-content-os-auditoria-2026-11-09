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

# Fetch the OpenAPI JSON spec
curl -s "$API_URL/openapi.json" -o "$OUTPUT_DIR/openapi.json"

# Generate TypeScript declarations
npx openapi-typescript "$OUTPUT_DIR/openapi.json" --output "$OUTPUT_DIR/$OUTPUT_FILE"

echo "✅ Generated TypeScript types at $OUTPUT_DIR/$OUTPUT_FILE"
