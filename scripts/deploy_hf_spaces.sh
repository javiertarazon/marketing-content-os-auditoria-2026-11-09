#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -n "${HF_CLI:-}" ]]; then
  HF_COMMAND="$HF_CLI"
elif command -v hf >/dev/null 2>&1; then
  HF_COMMAND="$(command -v hf)"
elif [[ -x "${HOME}/.local/bin/hf" ]]; then
  HF_COMMAND="${HOME}/.local/bin/hf"
else
  echo "No se encontro HF CLI. Instala con: uv tool install huggingface_hub" >&2
  exit 1
fi

IDENTITY="$("$HF_COMMAND" auth whoami --format json)"
USERNAME="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["user"])' <<<"$IDENTITY")"
SPACE_ID="${USERNAME}/voice-studio-agent"
VOICE_STUDIO_SOURCE="${ROOT}/third_party/VoiceStudio"
if [[ ! -d "${VOICE_STUDIO_SOURCE}/omnivoice" || ! -f "${VOICE_STUDIO_SOURCE}/LICENSE" ]]; then
  echo "No se encontro third_party/VoiceStudio; ejecuta scripts/clone_third_party.py primero." >&2
  exit 1
fi
SOURCE_COMMIT="$(git -C "$VOICE_STUDIO_SOURCE" rev-parse --short HEAD)"
STAGING_DIR="$(mktemp -d)"
trap 'rm -rf -- "$STAGING_DIR"' EXIT
cp -a "${ROOT}/apps/voice-studio-space/." "$STAGING_DIR/"
cp -a "${VOICE_STUDIO_SOURCE}/omnivoice" "${STAGING_DIR}/omnivoice"
cp "${VOICE_STUDIO_SOURCE}/LICENSE" "${STAGING_DIR}/VOICESTUDIO-LICENSE"
find "$STAGING_DIR" -type d \( -name __pycache__ -o -name .pytest_cache \) -prune -exec rm -rf -- {} +
find "$STAGING_DIR" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete

echo "Preparando el Space privado ZeroGPU ${SPACE_ID}"
"$HF_COMMAND" repos create "$SPACE_ID" \
  --type space \
  --sdk gradio \
  --private \
  --flavor zero-a10g \
  --exist-ok
"$HF_COMMAND" repos settings "$SPACE_ID" --type space --private
"$HF_COMMAND" spaces settings "$SPACE_ID" --hardware zero-a10g

echo "Publicando la app Gradio de VoiceStudio en ${SPACE_ID}"
"$HF_COMMAND" upload "$SPACE_ID" "$STAGING_DIR" . \
  --type space \
  --commit-message "Deploy VoiceStudio ZeroGPU agent (${SOURCE_COMMIT})"

printf '\nSpace publicado: https://huggingface.co/spaces/%s\n' "$SPACE_ID"
echo "Comprueba el build. El Space debe permanecer privado y usar ZeroGPU."
