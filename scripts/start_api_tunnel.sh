#!/usr/bin/env bash
# Launch a Cloudflare Tunnel for the FastAPI service.
# Requires cloudflared to be installed and a tunnel named "api" configured.
# See https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/create-tunnel/

set -euo pipefail

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared not found. Install it before running this script."
  exit 1
fi

# Run the tunnel in the foreground so the script can be stopped with Ctrl+C.
# The tunnel configuration should be created once with:
#   cloudflared tunnel create api
#   cloudflared tunnel route dns <tunnel-id> api.example.com
# and the credentials file placed in ~/.cloudflared.
cloudflared tunnel run api
