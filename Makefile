# Makefile for Development workflow

.PHONY: dev up generate-types open-dashboard

default: dev

dev: up generate-types open-dashboard

up:
	@echo "🚀 Starting Docker Compose services..."
	docker compose -f docker-compose.dev.yml up -d

# Generate TypeScript client types from the FastAPI OpenAPI spec
generate-types:
	@echo "⚙️ Waiting for the API to be healthy..."
	@bash scripts/wait_for_api.sh
	@echo "⚙️ Generating TypeScript types..."
	bash scripts/generate_api_types.sh

# Open the Dashboard in the default browser (if possible)
open-dashboard:
	@echo "🌐 Opening Dashboard at http://localhost:3000"
	@if command -v xdg-open > /dev/null; then xdg-open http://localhost:3000; \
	elif command -v open > /dev/null; then open http://localhost:3000; \
	else echo "Please open http://localhost:3000 in your browser"; fi

# Start Cloudflare tunnel for API

tunnel:
	@echo "🚧 Starting Cloudflare tunnel for API"
	bash scripts/start_api_tunnel.sh
	@echo "🌐 Opening Dashboard at http://localhost:3000"
	@if command -v xdg-open > /dev/null; then xdg-open http://localhost:3000; \
	elif command -v open > /dev/null; then open http://localhost:3000; \
	else echo "Please open http://localhost:3000 in your browser"; fi
