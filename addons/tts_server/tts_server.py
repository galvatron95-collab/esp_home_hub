"""TTS WebSocket server with READY-gated handshake.

Each WebSocket connection has its own request queue and a `ready` flag.
The protocol is:

  client → text       "say this"
  server → audio chunks (4096-byte binary frames)
  server → 0-byte binary frame  (EOS)
  server waits, holding the connection open
  client → text       "READY"
  server → accepts next request

MQTT messages on topic ``tts/response`` enqueue a synthesis request on every
currently-connected client. If a client is mid-stream or waiting for READY,
the request waits in that client's queue. There is no time-based timeout on
the READY wait; if a client never sends READY, its queue grows but the
connection stays open. The patient end of the wire is the server.
"""

import asyncio
import logging
import signal
from dataclasses import dataclass, field

import azure.cognitiveservices.speech as speechsdk
import paho.mqtt.client as mqtt
import websockets

try:
    from azure_secrets import AZURE_KEY, AZURE_REGION
except ImportError:
    AZURE_KEY = None
    AZURE_REGION = None

WS_HOST = "0.0.0.0"
WS_PORT = 8765
MQTT_HOST = "localhost"
MQTT_PORT = 1883
MQTT_TOPIC = "tts/response"
CHUNK_SIZE = 4096
READY_MARKER = "READY"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("tts-server")


@dataclass(eq=False)
class ClientState:
    """Per-connection state.

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


async def stream_audio(state: ClientState, audio: bytes) -> None:
    """Send `audio` as 4096-byte chunks plus a 0-byte EOS marker, then
    clear `ready` and wait for the client's READY frame before returning."""
    peer = state.websocket.remote_address
    state.ready.clear()
    for i in range(0, len(audio), CHUNK_SIZE):
        await state.websocket.send(audio[i : i + CHUNK_SIZE])
    await state.websocket.send(b"")
    log.info("%s: EOS sent (%d bytes), waiting for READY", peer, len(audio))
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
            await stream_audio(state, audio)
    except websockets.ConnectionClosed:
        log.info("%s: connection closed, sender exiting", peer)


async def ws_handler(websocket) -> None:
    """Per-connection dispatcher. Inbound text is either a synthesis
    request (queued for the sender) or the READY marker (releases the
    sender's wait)."""
    peer = websocket.remote_address
    state = ClientState(websocket=websocket)
    clients.add(state)
    log.info("Client connected: %s (total=%d)", peer, len(clients))
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
        log.info("Client disconnected: %s (total=%d)", peer, len(clients))


def start_mqtt(loop: asyncio.AbstractEventLoop) -> mqtt.Client:
    """Start paho MQTT in its own thread; bridge messages onto `loop`.

    Each MQTT message enqueues the text on every currently-connected
    client's queue. The per-client sender task gates actual delivery
    on the READY handshake.
    """
    client = mqtt.Client()

    def on_connect(_c, _u, _flags, rc):
        if rc == 0:
            log.info("MQTT connected to %s:%d", MQTT_HOST, MQTT_PORT)
            client.subscribe(MQTT_TOPIC)
            log.info("MQTT subscribed to %r", MQTT_TOPIC)
        else:
            log.error("MQTT connect failed rc=%d", rc)

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

    async with websockets.serve(ws_handler, WS_HOST, WS_PORT):
        log.info("WebSocket server listening on ws://%s:%d", WS_HOST, WS_PORT)
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
