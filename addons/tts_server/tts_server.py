"""TTS WebSocket server with READY-gated handshake.

Port 8765 — TTS (MQTT-driven):
  Each WebSocket connection has its own request queue and a `ready` flag.
  The protocol is:

    MQTT message arrives  →  enqueue text on all connected clients
    server → audio chunks (4096-byte binary frames)
    server → 0-byte binary frame  (EOS)
    server waits, holding the connection open
    client → text  "READY"
    server → accepts next request

Port 8766 — STT round-trip (ESP32-driven):
  The ESP32 opens a connection, sends the finalised transcript as one
  text frame, and holds the connection open. The server:

    client → text  "<transcript>"
    server calls HA conversation.process (Claude) → response text
    server synthesises response text via Azure TTS → raw PCM
    server → audio chunks (4096-byte binary frames)
    server → 0-byte binary frame  (EOS)
    server waits for client READY
    client → text  "READY"
    server closes the connection

  One transcript per connection. The connection is closed after the
  READY handshake completes (or on any error).
"""

import asyncio
import json
import logging
import os
import signal
import urllib.error
import urllib.request
from dataclasses import dataclass, field

import azure.cognitiveservices.speech as speechsdk
import paho.mqtt.client as mqtt
import websockets

# Azure creds: prefer env vars (set by the HA add-on's run.sh from
# /data/options.json), fall back to azure_secrets.py for local dev
# on machines where the file exists. Either source is honoured;
# whichever is non-empty wins, with env vars taking priority.
AZURE_KEY = os.getenv("AZURE_KEY") or None
AZURE_REGION = os.getenv("AZURE_REGION") or None
if not AZURE_KEY or not AZURE_REGION:
    try:
        from azure_secrets import (
            AZURE_KEY as _SECRETS_KEY,
            AZURE_REGION as _SECRETS_REGION,
        )
        AZURE_KEY = AZURE_KEY or _SECRETS_KEY
        AZURE_REGION = AZURE_REGION or _SECRETS_REGION
    except ImportError:
        pass

WS_HOST = "0.0.0.0"
WS_PORT = int(os.getenv("WS_PORT", "8765"))
STT_PORT = int(os.getenv("STT_PORT", "8766"))
MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME") or None
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD") or None
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "tts/response")
SUPERVISOR_TOKEN = os.getenv("SUPERVISOR_TOKEN") or None

HA_EVENT_URL = "http://supervisor/core/api/events/esp32_transcript"
HA_CONVERSATION_URL = "http://supervisor/core/api/conversation/process"

# Conversation agent entity ID. "conversation.claude_conversation" uses
# the Anthropic integration; swap to "conversation.home_assistant" for
# the built-in intent engine as a fallback. Set via the add-on option
# ha_conversation_agent (run.sh exports HA_CONVERSATION_AGENT).
HA_CONVERSATION_AGENT = os.getenv(
    "HA_CONVERSATION_AGENT", "conversation.claude_conversation"
)

# Azure multilingual voice — speaks EN + ZH (and more) from one voice,
# so the server never has to pick a per-language voice for Claude's
# (possibly translated) response. Set via the add-on option azure_voice.
AZURE_VOICE = os.getenv("AZURE_VOICE", "en-US-AvaMultilingualNeural")

CHUNK_SIZE = 4096
READY_MARKER = "READY"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("tts-server")


@dataclass(eq=False)
class ClientState:
    """Per-connection state for the TTS (port 8765) clients.

    `queue` holds pending text-to-synthesise strings. `ready` is True
    between connection-open and the first send, then again only after
    the client acknowledges drain with a READY frame.

    `eq=False` keeps the default id-based hash so instances can live in
    a set; per-connection state intentionally has identity semantics.
    """
    websocket: object
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    ready: asyncio.Event = field(default_factory=asyncio.Event)

    def __post_init__(self) -> None:
        self.ready.set()


clients: set[ClientState] = set()


def synthesize(text: str) -> bytes:
    """Block-call Azure TTS and return raw 16 kHz 16-bit mono PCM bytes.

    Returns empty bytes on failure so the caller can still send the EOS
    marker and continue the handshake without dropping the connection.
    """
    if not AZURE_KEY or not AZURE_REGION:
        log.error("Azure credentials missing; skipping synthesis")
        return b""

    speech_config = speechsdk.SpeechConfig(
        subscription=AZURE_KEY, region=AZURE_REGION
    )
    speech_config.speech_synthesis_voice_name = AZURE_VOICE
    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Raw16Khz16BitMonoPcm
    )
    synthesizer = speechsdk.SpeechSynthesizer(
        speech_config=speech_config, audio_config=None
    )
    result = synthesizer.speak_text_async(text).get()
    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        return result.audio_data
    log.error(
        "Azure TTS failed: reason=%s details=%s",
        result.reason,
        getattr(result, "error_details", "<none>"),
    )
    return b""


async def stream_audio(websocket, audio: bytes) -> None:
    """Send `audio` as 4096-byte chunks plus a 0-byte EOS marker.

    Used by both the TTS sender task (port 8765) and the STT round-trip
    handler (port 8766). The caller supplies the ready synchronisation
    for the TTS path; for the STT path the READY wait is handled by the
    handler's own receive loop."""
    for i in range(0, len(audio), CHUNK_SIZE):
        await websocket.send(audio[i: i + CHUNK_SIZE])
    await websocket.send(b"")
    peer = websocket.remote_address
    log.info("%s: EOS sent (%d bytes total)", peer, len(audio))


async def stream_audio_and_wait_ready(state: ClientState, audio: bytes) -> None:
    """TTS-path wrapper: stream audio then gate on the ClientState ready event."""
    peer = state.websocket.remote_address
    state.ready.clear()
    await stream_audio(state.websocket, audio)
    log.info("%s: waiting for READY", peer)
    await state.ready.wait()
    log.info("%s: READY received, handshake complete", peer)


async def sender_task(state: ClientState) -> None:
    """Per-connection worker: pop text from the queue, synth, stream,
    wait for READY, repeat. Exits when the websocket closes."""
    peer = state.websocket.remote_address
    try:
        while True:
            text = await state.queue.get()
            log.info("%s: processing %r", peer, text[:80])
            audio = await asyncio.to_thread(synthesize, text)
            await stream_audio_and_wait_ready(state, audio)
    except websockets.ConnectionClosed:
        log.info("%s: connection closed, sender exiting", peer)


async def ws_handler(websocket) -> None:
    """Per-connection dispatcher for port 8765 (MQTT-driven TTS).
    Inbound text is either a synthesis request (queued for the sender)
    or the READY marker (releases the sender's wait)."""
    peer = websocket.remote_address
    state = ClientState(websocket=websocket)
    clients.add(state)
    log.info("TTS client connected: %s (total=%d)", peer, len(clients))
    sender = asyncio.create_task(sender_task(state))
    try:
        async for message in websocket:
            if isinstance(message, bytes):
                log.warning("%s: ignoring binary frame", peer)
                continue
            if message == READY_MARKER:
                state.ready.set()
                continue
            log.info("%s: queued %r (q=%d)", peer, message[:80], state.queue.qsize() + 1)
            await state.queue.put(message)
    except websockets.ConnectionClosed:
        pass
    finally:
        sender.cancel()
        try:
            await sender
        except (asyncio.CancelledError, websockets.ConnectionClosed):
            pass
        clients.discard(state)
        log.info("TTS client disconnected: %s (total=%d)", peer, len(clients))


def post_transcript_event(text: str) -> int:
    """POST one transcript to the HA Core event API via the Supervisor
    proxy. Returns the HTTP status code, or 0 on failure. Blocking;
    callers offload it with asyncio.to_thread.

    This is now a secondary/logging action in the STT round-trip path —
    the primary action is call_conversation_process(). The event is
    still fired so HA automations can observe transcripts independently.
    """
    if not SUPERVISOR_TOKEN:
        log.error("SUPERVISOR_TOKEN missing; cannot fire HA event")
        return 0
    body = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(
        HA_EVENT_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        log.error("HA event POST HTTPError: %s", e.code)
        return e.code
    except OSError as e:
        log.error("HA event POST failed: %s", e)
        return 0


def call_conversation_process(text: str, language: str) -> str | None:
    """POST the transcript to HA's conversation/process API and return
    the response text, or None on failure. Blocking; callers offload it
    with asyncio.to_thread.

    The conversation agent is selected by HA_CONVERSATION_AGENT
    (default: conversation.claude_conversation). HA resolves intent
    (device control) or falls back to the LLM for free-form responses.
    `language` is the INPUT language (what was spoken: 'en'/'zh') so the
    agent knows the source language; Claude may still translate, and the
    multilingual Azure voice speaks whatever language it returns.
    """
    if not SUPERVISOR_TOKEN:
        log.error("SUPERVISOR_TOKEN missing; cannot call conversation API")
        return None
    body = json.dumps({
        "text": text,
        "agent_id": HA_CONVERSATION_AGENT,
        "language": language,
    }).encode("utf-8")
    req = urllib.request.Request(
        HA_CONVERSATION_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            # HA conversation/process returns:
            # {"response": {"speech": {"plain": {"speech": "..."}}, ...}, ...}
            speech = (
                data
                .get("response", {})
                .get("speech", {})
                .get("plain", {})
                .get("speech")
            )
            log.info("Conversation response: %r", (speech or "")[:200])
            return speech
    except urllib.error.HTTPError as e:
        log.error("Conversation API HTTPError: %s", e.code)
        return None
    except (OSError, json.JSONDecodeError, KeyError) as e:
        log.error("Conversation API failed: %s", e)
        return None


async def stt_handler(websocket) -> None:
    """Per-connection handler for port 8766 (ESP32 STT round-trip).

    Protocol:
      1. ESP32 sends one text frame: the finalised transcript.
      2. Server fires the esp32_transcript HA event (for automations).
      3. Server calls HA conversation/process → response text.
      4. Server synthesises response text → raw PCM.
      5. Server streams PCM + EOS back to ESP32.
      6. Server waits for READY from ESP32.
      7. Server closes the connection.

    One transcript per connection. Any failure is logged and the
    connection is closed; the ESP32 returns to idle either way.
    """
    peer = websocket.remote_address
    log.info("STT client connected: %s", peer)
    try:
        async for message in websocket:
            if isinstance(message, bytes):
                log.warning("%s: STT ignoring binary frame", peer)
                continue

            if message == READY_MARKER:
                # ESP32 acknowledged the audio — close cleanly.
                log.info("%s: READY received, closing STT connection", peer)
                break

            # Frame is JSON: {"text": "<transcript>", "lang": "en"|"zh"}.
            try:
                frame = json.loads(message)
                transcript = frame["text"]
                language = frame.get("lang", "en")
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                log.error("%s: bad transcript frame %r (%s); closing",
                          peer, message[:200], e)
                break
            if not transcript:
                log.warning("%s: empty transcript in frame; closing", peer)
                break
            log.info("%s: transcript %r lang=%s", peer, transcript[:200], language)

            # Fire the HA event for any automation observers (non-blocking
            # from the ESP32's perspective — we don't wait on the result
            # before proceeding to conversation).
            asyncio.create_task(
                asyncio.to_thread(post_transcript_event, transcript)
            )

            # Call HA conversation agent (Claude) — this is the blocking
            # step; 1-3 seconds typical.
            log.info("%s: calling conversation agent %s", peer, HA_CONVERSATION_AGENT)
            response_text = await asyncio.to_thread(
                call_conversation_process, transcript, language
            )

            if not response_text:
                log.error("%s: no response from conversation agent; closing", peer)
                break

            log.info("%s: synthesising %r", peer, response_text[:200])
            audio = await asyncio.to_thread(synthesize, response_text)

            if not audio:
                log.error("%s: synthesis produced no audio; closing", peer)
                break

            # Stream PCM + EOS, then wait for READY (arrives in the next
            # iteration of this async-for loop and hits the break above).
            await stream_audio(websocket, audio)
            log.info("%s: waiting for READY", peer)

    except websockets.ConnectionClosed:
        pass
    finally:
        log.info("STT client disconnected: %s", peer)


def start_mqtt(loop: asyncio.AbstractEventLoop) -> mqtt.Client:
    """Start paho MQTT in its own thread; bridge messages onto `loop`.

    Each MQTT message enqueues the text on every currently-connected
    TTS client's queue. The per-client sender task gates actual delivery
    on the READY handshake.
    """
    client = mqtt.Client()
    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD or "")

    def on_connect(_c, _u, _flags, rc):
        if rc == 0:
            log.info("MQTT connected to %s:%d as %r", MQTT_HOST, MQTT_PORT,
                     MQTT_USERNAME or "<anonymous>")
            client.subscribe(MQTT_TOPIC)
            log.info("MQTT subscribed to %r", MQTT_TOPIC)
        else:
            log.error("MQTT connect failed rc=%d (5=auth failure)", rc)

    def on_message(_c, _u, msg):
        try:
            text = msg.payload.decode("utf-8")
        except UnicodeDecodeError:
            log.error("MQTT payload not UTF-8; dropping")
            return
        log.info("MQTT %s: %r (fanout=%d)", msg.topic, text[:80], len(clients))

        async def fanout():
            for state in list(clients):
                await state.queue.put(text)

        asyncio.run_coroutine_threadsafe(fanout(), loop)

    client.on_connect = on_connect
    client.on_message = on_message
    try:
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    except OSError as e:
        log.error("MQTT connect threw %s; running without MQTT", e)
        return client
    client.loop_start()
    return client


async def main() -> None:
    loop = asyncio.get_running_loop()
    mqtt_client = start_mqtt(loop)

    stop = loop.create_future()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set_result, None)
        except NotImplementedError:
            pass

    async with websockets.serve(ws_handler, WS_HOST, WS_PORT), \
            websockets.serve(stt_handler, WS_HOST, STT_PORT):
        log.info("WebSocket server listening on ws://%s:%d", WS_HOST, WS_PORT)
        log.info("STT round-trip listening on ws://%s:%d (agent=%s voice=%s)",
                 WS_HOST, STT_PORT, HA_CONVERSATION_AGENT, AZURE_VOICE)
        if not AZURE_KEY or not AZURE_REGION:
            log.warning(
                "azure_secrets.py not found or incomplete; TTS calls will "
                "return empty audio. Copy azure_secrets.py.example and fill "
                "in real values."
            )
        try:
            await stop
        finally:
            log.info("Shutting down")
            mqtt_client.loop_stop()
            try:
                mqtt_client.disconnect()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
