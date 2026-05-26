# tts-server

Standalone WebSocket TTS service. Generates audio with Azure Speech and
streams it to one or more connected clients (ESP32 in the target case)
using a **READY-gated handshake** so the server never moves on until the
client has actually drained the audio it was sent. Listens for synthesis
requests on MQTT (`tts/response` on `localhost:1883`) or directly on the
WebSocket.

## Why the handshake exists

Sending audio over WebSocket and hoping the client keeps up is fragile.
TCP backpressure stops bytes mid-flight, but it does not stop the *server*
from concluding it has delivered everything and moving on to the next
request. If the next request arrives before the client has finished
playing the current one, the client pipeline is asked to absorb two
streams' worth of audio at once and breaks.

This server avoids that by waiting for an explicit application-layer
acknowledgement from the client between requests. Each connection has its
own state machine: the server sends audio + EOS, then **holds** until the
client sends a `READY` text frame. Only then does the server pull the next
queued request. No time-based timeout — the patient end of the wire is
the server.

## Wire protocol

| Step | Direction | Frame | Meaning |
|---|---|---|---|
| 1 | client → server | text | "synthesise this and send it to me" |
| 2 | server → client | binary, ≤ 4096 bytes | audio chunk (raw 16 kHz / 16-bit / mono PCM) |
| ... | server → client | binary | more audio chunks |
| 3 | server → client | binary, 0 bytes | EOS marker — no more audio for this request |
| 4 | server → client | (silence) | server is now **gated**, waiting for READY |
| 5 | client → server | text `READY` | "I've drained the audio, you may proceed" |
| 6 | server → client | (next queued request, back to step 2) | or no traffic if queue empty |

The `READY` literal is case-sensitive and must be sent as a text frame,
not binary.

Any text frame other than `READY` is treated as a new synthesis request
and queued. The client can send the next request *before* sending READY
for the previous one; the server will queue it and process it in order
after the previous request's READY arrives.

## MQTT path

The server subscribes to `tts/response` on `localhost:1883`. Each message
on that topic is fanned out to every currently-connected client by
enqueueing the payload on each client's per-connection queue. The
READY-gating is per-client: a slow client doesn't block fast clients, but
a slow client *does* accumulate a queue.

## Layout

```
tts-server/
  tts_server.py            # WebSocket + MQTT + Azure TTS, READY-gated
  test_client.py           # Mock ESP32 with default/no-ready/gate-test modes
  azure_secrets.py.example # Template; copy to azure_secrets.py
  requirements.txt
  README.md
  .gitignore
```

`azure_secrets.py` (your real Azure key + region) is git-ignored. Do not
commit it.

## Setup

From this directory:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

(Use `source .venv/bin/activate` on Linux/macOS.)

## Configure Azure (when ready)

```powershell
copy azure_secrets.py.example azure_secrets.py
# edit azure_secrets.py and put real values in AZURE_KEY / AZURE_REGION
```

The server runs without `azure_secrets.py`, but every synthesis call will
log an error and stream an empty audio response (still followed by EOS, so
clients stay healthy and the handshake still completes).

## Run

```powershell
python tts_server.py
```

Logs go to stdout. Expect:

- `WebSocket server listening on ws://0.0.0.0:8765`
- `MQTT connected to localhost:1883` and `MQTT subscribed to 'tts/response'`,
  or `MQTT connect threw ...; running without MQTT` if no broker is up.

## Test client modes

The mock ESP32 client has three modes, exposed as flags.

### Default — happy path

```powershell
python test_client.py "Hello, doorbell."
```

Connects, sends one prompt, drains audio, sends `READY`, exits. Writes
`output.wav`. Server log should show `EOS sent (...) waiting for READY`
then `READY received, handshake complete`.

### `--gate-test` — prove the gate holds

```powershell
python test_client.py "Short test." --gate-test --gate-release-after 3
```

Sends two prompts back-to-back. Drains the first audio stream, then
**does not send READY**. Sends the second prompt anyway. Waits the
specified number of seconds, then sends READY. Verifies the second
stream's audio arrives only *after* the READY was sent.

Server log should show the second request `queued ... (q=1)` while the
gate is held, then traffic only after the READY arrives.

### `--no-ready` — confirm the server actually waits

```powershell
python test_client.py "Single shot, no ack." --no-ready
```

Sends one prompt, drains audio, **closes the connection without sending
READY**. The server's log should report `waiting for READY` and then
notice the disconnect. Useful for confirming the server isn't
short-circuiting the wait somehow.

## MQTT fan-out check

With the server running and at least one WebSocket client connected, in
another shell:

```powershell
mosquitto_pub -h localhost -t tts/response -m "Hello from MQTT."
```

The text gets queued on every currently-connected client's per-connection
queue. Clients with the gate held will see it after their next READY;
idle clients get it immediately.

## Testing workflow

### Stage 1 — local, no Azure credentials

You can verify the handshake and plumbing without an Azure key:

- Server starts and binds port 8765.
- `python test_client.py "anything"` connects, sends text, receives one
  0-byte EOS frame, sends READY, and exits. The client will print
  `FAIL: no audio chunks received` — that is the correct outcome with no
  credentials and confirms the WebSocket plumbing, EOS marker, and READY
  handshake all work.
- `--gate-test` works the same way (no audio chunks, but the timing
  assertion still proves the second stream waits for READY).

### Stage 2 — with Azure credentials

1. `copy azure_secrets.py.example azure_secrets.py` and fill in real
   values.
2. Restart the server.
3. `python test_client.py "Hello, doorbell."` — expect chunks of up to
   4096 bytes, a final 0-byte frame, and `OK: handshake completed
   cleanly`.
4. Play `output.wav` in any audio player to sanity-check the voice.
5. `python test_client.py "Two prompts." --gate-test` — expect two
   `output_*.wav` files, the second one's arrival timed to your
   `--gate-release-after` value, and `OK: gate held second request until
   READY arrived`.

## Audio format contract

The ESP32 speaker path consumes raw PCM, so the server emits:

| Property        | Value                  |
|-----------------|------------------------|
| Sample rate     | 16 000 Hz              |
| Bit depth       | 16-bit signed little-endian |
| Channels        | 1 (mono)               |
| Container       | none — raw PCM bytes   |
| Chunk size      | up to 4096 bytes       |
| End-of-stream   | one zero-length binary frame |
| Acknowledgement | one text frame `READY` |

`test_client.py` wraps the raw PCM in a WAV header before writing
`output.wav` purely so you can play it back; the wire format on the
WebSocket is raw PCM, not WAV.
