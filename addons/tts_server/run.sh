#!/usr/bin/env sh
# Entrypoint for the tts_server add-on. Reads add-on options from
# /data/options.json and exports them as environment variables, then
# execs the Python server. Uses plain Python for JSON parsing because
# this image is python:3.11-slim (Debian), not an HA base image with
# bashio.

set -e

OPTIONS_FILE="/data/options.json"

# Read one option from /data/options.json. Empty string if absent.
get_opt() {
    python3 -c "import json,sys; print(json.load(open('${OPTIONS_FILE}')).get('$1',''))" 2>/dev/null || echo ""
}

if [ -f "${OPTIONS_FILE}" ]; then
    export AZURE_KEY="$(get_opt azure_key)"
    export AZURE_REGION="$(get_opt azure_region)"
    export MQTT_HOST="$(get_opt mqtt_host)"
    export MQTT_PORT="$(get_opt mqtt_port)"
    export MQTT_USERNAME="$(get_opt mqtt_username)"
    export MQTT_PASSWORD="$(get_opt mqtt_password)"
    export MQTT_TOPIC="$(get_opt mqtt_topic)"
    export WS_PORT="$(get_opt ws_port)"
    export HA_CONVERSATION_AGENT="$(get_opt ha_conversation_agent)"
    export AZURE_VOICE="$(get_opt azure_voice)"
else
    echo "WARNING: ${OPTIONS_FILE} not found; relying on existing env vars."
fi

if [ -z "${AZURE_KEY}" ] || [ -z "${AZURE_REGION}" ]; then
    echo "WARNING: AZURE_KEY or AZURE_REGION is empty; TTS calls will return empty audio."
fi

echo "Starting tts_server: TTS ws://0.0.0.0:${WS_PORT:-8765}, STT round-trip ws://0.0.0.0:${STT_PORT:-8766} (agent=${HA_CONVERSATION_AGENT:-conversation.claude_conversation}), mqtt=${MQTT_HOST:-core-mosquitto}:${MQTT_PORT:-1883} topic=${MQTT_TOPIC:-tts/response}"

cd /app
exec python3 tts_server.py
