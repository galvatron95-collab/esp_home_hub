#!/usr/bin/with-contenv bashio
# Entrypoint for the tts_server add-on. Reads add-on options from
# /data/options.json via bashio, exports them as environment
# variables, and execs the Python server.

set -e

export AZURE_KEY="$(bashio::config 'azure_key')"
export AZURE_REGION="$(bashio::config 'azure_region')"
export MQTT_HOST="$(bashio::config 'mqtt_host')"
export MQTT_PORT="$(bashio::config 'mqtt_port')"
export MQTT_USERNAME="$(bashio::config 'mqtt_username')"
export MQTT_PASSWORD="$(bashio::config 'mqtt_password')"
export MQTT_TOPIC="$(bashio::config 'mqtt_topic')"
export WS_PORT="$(bashio::config 'ws_port')"

if [ -z "${AZURE_KEY}" ] || [ -z "${AZURE_REGION}" ]; then
    bashio::log.warning "AZURE_KEY or AZURE_REGION is empty; TTS calls will return empty audio."
fi

bashio::log.info "Starting tts_server on ws://0.0.0.0:${WS_PORT}, mqtt=${MQTT_HOST}:${MQTT_PORT} topic=${MQTT_TOPIC}"

cd /app
exec python3 tts_server.py
