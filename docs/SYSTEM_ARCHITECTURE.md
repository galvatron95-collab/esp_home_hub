# System architecture — voice terminal + HA add-on

This document describes the voice terminal system as it works today
across the two repos and the HA instance. It is the single place to
verify cross-repo invariants before starting wake word, translation
streaming, or any other feature that touches more than one component.

The per-repo CLAUDE.md files remain authoritative for their own change
loops. This document sits above them: it describes the interfaces
between components, the shared invariants both repos depend on, and
the add-on's contract (which has no CLAUDE.md coverage).

Last verified against code: 2026-06-11.

---

## 1. System topology

Two nodes, one LAN, one cloud service reached by the device.

```
                    ┌─────────────────────────────────────┐
                    │        Home Assistant Core           │
                    │           (HAOS 17.3)                │
                    │                                      │
                    │  conversation.claude_conversation    │
                    │    intent matching → device control  │
                    │    OR LLM fallback → response text   │
                    └────────┬────────────────────────────┘
                             │
              Supervisor API │
              + Core API     │
                             │
                    ┌────────┴──────┐
                    │  TTS Server   │
                    │  (HA add-on)  │
                    │  tts_server.py│
                    │               │
                    │  :8765 MQTT   │
                    │  :8766 STT RT │
                    └───────┬───────┘
                            │
              ws://:8766    │
              (LAN only)    │
                            │
                    ┌───────┴───────────────────────────────┐
                    │  Voice terminal                        │
                    │  (ESP-IDF 6.0, ESP32-S3-N16R8)         │
                    │  mic_capture project                   │
                    │                                        │
                    │  Deepgram STT (TLS, cloud)              │
                    │  HA add-on link (WS, LAN)               │
                    │  Speaker + mic always-on pipeline       │
                    └────────────────────────────────────────┘
```

**Ownership boundaries:**

| Component | Repo | Governed by |
|---|---|---|
| Voice terminal firmware | `esp_ai_chat_bot/mic_capture` | `mic_capture/CLAUDE.md` |
| TTS server add-on | `esp_home_hub/addons/tts_server/` | This document (no CLAUDE.md coverage) |
| HA automations + integrations | HA UI (not in any repo) | Operator |

---

## 2. End-to-end data paths

### 2.1 Voice terminal: button press to spoken response

This is the primary path. Strictly sequential — no concurrent
capture and playback.

```
Button press (GPIO 21, active low)
  │
  ▼
STATE_STT
  │
  ├─ stt_producer task (Core 1, priority 6)
  │    stt_audio_helper: mic_read() → 32-bit stereo → 16-bit mono
  │    conversion: >>8 (24-valid-in-32 to 16-bit), >>2 downmix
  │    chunk: 4096 bytes raw → 2048 bytes mono 16-bit (1024 samples)
  │    output → stt_pipeline ring (16 slots × 1024 bytes, internal DRAM)
  │
  ├─ deepgram_minimal consumer (chatbot task, Core 0)
  │    128 ms cadence, batches 4 × 512-sample pops per send
  │    sends via esp_websocket_client to Deepgram Nova-3 (TLS)
  │    Deepgram params: model=nova-3, sample_rate=16000, channels=1,
  │      encoding=linear16, language=<en|zh from mode button>
  │    receives transcript via WS event handler
  │    session ends: button press (toggle) or 60s hard cap
  │    sends {"type":"Finalize"}, waits for final result,
  │    sends {"type":"CloseStream"}, destroys WS client
  │
  ├─ Deepgram WS is FULLY CLOSED before next step
  │
  ▼
ha_link_round_trip() — one WS to add-on :8766
  │
  ├─ open ws://192.168.50.125:8766 (15s connect timeout)
  ├─ send JSON text frame: {"text":"<transcript>","lang":"<en|zh>"}
  │    (snprintf, no JSON escaping — tracked debt, safe for Nova-3 output)
  ├─ hold connection open across HA think-time (1-5s typical)
  │
  ├─ receive binary PCM frames → tts_ring_write_blocking()
  │    all-or-nothing, 1.5s timeout (ring overflow failsafe only)
  │    TCP backpressure handles within-stream pacing
  ├─ receive 0-byte binary frame = EOS
  │
  ├─ set SPK_BIT_FINALISE on speaker_consumer event group
  ├─ wait SPK_BIT_DONE (30s diagnostic ceiling)
  │
  ├─ send "READY" text frame (acknowledges playback complete)
  ├─ close WS, return to STATE_IDLE
  │
  ▼
STATE_IDLE (waiting for next button press or mode toggle)
```

### 2.2 Add-on: transcript to spoken response

The add-on is the bridge between the device and HA. It runs in a
Docker container on the HA host (ThinkCentre, 192.168.50.125).

```
Port 8766 stt_handler (one connection per transcript):

  1. Receive text frame from ESP32
  2. Parse JSON {"text": "<transcript>", "lang": "en"|"zh"}
  3. Fire esp32_transcript HA event (async, secondary — for automations)
       POST http://supervisor/core/api/events/esp32_transcript
       Auth: Bearer $SUPERVISOR_TOKEN
  4. Call HA conversation/process (blocking, 1-30s)
       POST http://supervisor/core/api/conversation/process
       Body: {"text": "<transcript>", "agent_id": "<agent>", "language": "<lang>"}
       Agent default: conversation.claude_conversation (Anthropic integration)
       Response: .response.speech.plain.speech → the text to speak
  5. Azure TTS synthesis (blocking, ~1-3s)
       Voice: en-US-AvaMultilingualNeural (speaks EN + ZH from one voice)
       Format: Raw16Khz16BitMonoPcm
       Returns: raw PCM bytes
  6. Stream PCM as 4096-byte binary frames
  7. Send 0-byte binary frame (EOS)
  8. Wait for "READY" text frame from client
  9. Close connection
```

The `language` field from the ESP32's frame is passed to the HA
conversation API so the agent knows the input language. The
multilingual Azure voice handles whatever language the agent responds
in — no per-language voice selection needed.

Port 8765 is a separate MQTT-driven TTS path (subscribe to
`tts/response`, fan out to connected clients). Not currently used by
the voice terminal but available for broadcast scenarios (e.g. HA
automation triggers a spoken announcement).

---

## 3. Shared invariants

These invariants are depended on by code in both repos. Violating
them in either repo breaks the system.

### 3.1 Ring buffer backpressure (tts_ring)

The `tts_ring` in `mic_capture/main/tts_ring.c` is the single data
path from network-received PCM to the speaker. Both the retired
ElevenLabs path and the current HA add-on path feed it.

**Critical properties:**

- **5 MB SPIRAM allocation.** Leaves ~3 MB headroom for TLS, cJSON,
  helpers. Peak fill observed: 747 KB on the HA add-on path.
- **`tts_ring_write_blocking(src, len, wait_ticks)`**: all-or-nothing.
  Returns `len` on success, `0` on timeout. Never partial. The
  producer (WS event handler in `ha_link.c`) calls this with 1.5s
  timeout — it fires only if the ring genuinely overflows past 5 MB.
- **`tts_ring_read(dst, max_len, wait_ticks)`**: short-read OK.
  Returns bytes actually read; 0 on empty + timeout.
- **Release-before-wait pattern.** Both write_blocking and read
  release `s_mutex` before waiting on their respective semaphores
  (`s_space_avail` / `s_data_avail`). This is the canonical
  condition-variable pattern and is load-bearing — the consumer must
  be able to drain while the producer blocks on space.
- **SPSC by structural assumption.** One producer (WS event handler
  or parser task), one consumer (`speaker_consumer`). The sequential
  connection model guarantees only one producer exists at a time.
- **Watermarks (800 KB / 760 KB)** are passive canary values only.
  They do not drive any blocking primitive.

**The add-on depends on this:** `tts_server.py` streams 4096-byte
chunks as fast as TCP allows. Within-stream pacing is handled entirely
by TCP backpressure propagating through the WS library to
`tts_ring_write_blocking`. The add-on has no application-level flow
control beyond the READY handshake between streams.

### 3.2 READY handshake protocol

The READY handshake prevents between-stream overrun. It is the
contract between the add-on and the device.

```
Server streams PCM → 4096-byte binary frames
Server sends 0-byte binary frame (EOS)
Server holds connection open, waiting
Client finishes playing all buffered audio
Client sends "READY" text frame
Server accepts next request (port 8765) or closes (port 8766)
```

**Within a stream:** TCP backpressure handles pacing. The add-on
sends as fast as Azure TTS produces; the ESP32's WS library buffers
and `tts_ring_write_blocking` absorbs bursts.

**Between streams:** the READY marker is the flow control. The
server will not send more audio until the client has finished playing
the previous batch. This prevents the ring from accumulating unbounded
data across multiple responses.

On port 8766 (STT round-trip), the connection is closed after one
READY — one transcript per connection.

### 3.3 Audio format contract

PCM exchanged between the add-on and the device:

| Property | Value |
|---|---|
| Sample rate | 16,000 Hz |
| Bit depth | 16-bit signed |
| Channels | 1 (mono) |
| Byte order | Little-endian (ESP32-S3 native) |
| Encoding | Raw PCM (no container, no header) |

The add-on requests `Raw16Khz16BitMonoPcm` from Azure. The device
plays it through `speaker_stream_write()` which converts 16-bit mono
to 32-bit stereo for the ES8311 DAC (`int16_mono_to_int32_stereo`).

Deepgram receives the same format from the device (16 kHz, 16-bit,
mono, linear16, little-endian).

### 3.4 Sequential connection model

The device never has more than one WebSocket open at a time. Three
sockets per session total, strictly sequential:

```
1. Deepgram (TLS, cloud) — open during STT capture
2. HA add-on (plain WS, LAN) — open during transcript+response+playback
3. (none) — idle
```

This is structural, not coincidental. STT (mic-read producer on
Core 1) and TTS playback (speaker_consumer on Core 1) both need
Core 1 bandwidth. The sequential model makes their non-overlap a
guarantee of the state machine, not something coordinated by timers.

The ESP32-S3's mbedTLS cannot tolerate two concurrent TLS handshakes
from a cold start (confirmed: `PSA_ERROR_INVALID_SIGNATURE` on
build `_021`). The sequential model sidesteps this entirely.

### 3.5 Speaker pipeline (always-on, never stops)

The speaker pipeline runs from boot to power-off. No module stops,
resets, or reconfigures it after bringup.

```
tts_ring → speaker_consumer (Core 1, priority 5) → speaker_stream_write
           │                                         │
           │ 32 KB primer threshold                  │ int16 mono → int32 stereo
           │ 20 ms ring-read wait                    │ esp_codec_dev_write to ES8311
           │ silence fill when empty                 │
           │ 10 consecutive empties = FINALISE done  │
           │                                         │
           └─ 90 ms I2S DMA depth (6 desc × 240 frames × 8 bytes)
              must be fed continuously or stale samples loop
```

**speaker_stream_write** uses a static chunk buffer (no allocation).
**speaker_stream_finalise** writes 8 × 8192 zero bytes (65 KB) to
fully cycle the I2S TX DMA ring and eliminate residual clicks.

### 3.6 Core assignment

| Core | Responsibilities |
|---|---|
| Core 0 | WiFi, chatbot state machine, WS clients, display, all non-audio |
| Core 1 | Mic capture (stt_producer, pri 6), speaker playback (spk_cons, pri 5) |

During STT capture, both stt_producer and spk_cons run on Core 1.
stt_producer has higher priority and preempts spk_cons during mic
reads. Both touch the I2S peripheral (RX and TX channels
respectively). This is safe because they use different channels.

During playback, only spk_cons runs on Core 1 — stt_producer was
stopped before the HA link opened.

---

## 4. Add-on contract

The TTS server add-on (`esp_home_hub/addons/tts_server/`) has no
CLAUDE.md governance. This section serves as its contract until one
is written.

### 4.1 Identity

| Property | Value |
|---|---|
| Name | TTS Server |
| Version | 0.4.0 |
| Slug | tts_server |
| Base image | python:3.11-slim (Debian, glibc required for Azure SDK) |
| Architecture | amd64 only |
| Boot | auto (starts with HA) |

### 4.2 Ports

| Port | Protocol | Purpose |
|---|---|---|
| 8765/tcp | WebSocket | MQTT-driven TTS broadcast to connected clients |
| 8766/tcp | WebSocket | STT round-trip (ESP32 transcript in, PCM out) |

### 4.3 Dependencies

| Dependency | Purpose | Notes |
|---|---|---|
| `azure-cognitiveservices-speech` | TTS synthesis | Requires glibc; Alpine builds fail |
| `paho-mqtt` | MQTT subscription | Connects to core-mosquitto |
| `websockets` | WS server | Both ports |

### 4.4 HA API grants

- `hassio_api: true` — Supervisor API access
- `homeassistant_api: true` — Core API access (conversation/process,
  event firing). Both are required; `hassio_api` alone returns 401
  on Core API calls.

### 4.5 Configuration options

| Option | Default | Purpose |
|---|---|---|
| `azure_key` | (empty) | Azure Speech subscription key |
| `azure_region` | (empty) | Azure region (e.g. australiaeast) |
| `mqtt_host` | core-mosquitto | Mosquitto broker |
| `mqtt_port` | 1883 | MQTT port |
| `mqtt_username` | (empty) | HA user for MQTT auth (case-sensitive) |
| `mqtt_password` | (empty) | MQTT password |
| `mqtt_topic` | tts/response | Topic for TTS broadcast |
| `ws_port` | 8765 | TTS WebSocket port |
| `ha_conversation_agent` | conversation.claude_conversation | HA conversation agent entity |
| `azure_voice` | en-US-AvaMultilingualNeural | Azure TTS voice |

### 4.6 Invariants

1. **One transcript per connection on port 8766.** The stt_handler
   processes one transcript, streams the response, waits for READY,
   and closes. No connection reuse.
2. **Azure TTS is blocking.** `synthesize()` runs in a thread via
   `asyncio.to_thread`. The connection is held open across the
   synthesis time.
3. **Conversation API timeout is 30s.** Generous for Claude's
   intent + LLM processing. If the agent takes longer, the request
   fails and no audio is sent.
4. **Empty audio is non-fatal.** If Azure TTS fails, `synthesize()`
   returns `b""`, the handler logs the error and closes the
   connection. The ESP32 sees a WS close and returns to idle.
5. **MQTT auth is case-sensitive.** The HA user for MQTT must match
   exact case. `hapythonserver` (lowercase) is the current user.
6. **The HA event fire is async and secondary.** The
   `post_transcript_event()` call runs as an asyncio task; it does
   not block the conversation/synthesis path.

---

## 5. Translation pipeline

Translation is currently phase 1 (input language toggle). The
pipeline threads the language from button press to spoken response.

```
Mode button (GPIO 4, active low, ISR + binary semaphore)
  │ toggles s_input_lang_zh in chatbot.c
  │ LCD updates: "Listening: English" / "Listening: Chinese"
  │
  ▼
deepgram_minimal_run(transcript, 512, "en" | "zh")
  │ builds WS URL with language=en or language=zh
  │ Deepgram returns transcript in the requested language
  │
  ▼
ha_link_round_trip(transcript, "en" | "zh")
  │ JSON frame: {"text": "...", "lang": "en"|"zh"}
  │
  ▼
add-on stt_handler
  │ parses lang from frame
  │ passes language to call_conversation_process()
  │ HA conversation API receives language= in the POST body
  │ Claude conversation agent knows the input language
  │
  ▼
Azure TTS with AvaMultilingualNeural
  │ speaks whatever language the agent responded in
  │ no per-language voice selection needed
  │
  ▼
PCM back to ESP32 (same path as English)
```

**What's missing for full translation streaming:**
- Dedicated translation modes (paired input/output languages) —
  currently deferred to v2
- Translation-aware system prompt in the HA conversation agent
- Language display on the response side (device doesn't know what
  language the response is in)

---

## 6. Wake word integration points

Wake word is currently refused (mic_capture CLAUDE.md refusal #4) and
listed as "Blocked / deferred" in PROJECT_STATE.md. This section
documents where it would integrate, not a design proposal.

**Tap point:** `stt_audio_helper` output. The always-on mic pipeline
continuously produces 16-bit mono PCM at 16 kHz. Wake word detection
needs to consume this stream without disrupting the existing pipeline.
The `stt_audio_helper` is already instance-safe (caller-owned struct,
no static state), so a wake word task could own its own helper
instance.

**Activation model change:** currently, GPIO 21 button press starts
STATE_STT. Wake word would replace the button as the activation
trigger. The mode button (GPIO 4) for language toggle is orthogonal
and would remain.

**Core assignment question:** wake word detection is continuous audio
processing and should run on Core 1 (where all audio lives). It would
need to coexist with spk_cons (priority 5). During active STT capture,
stt_producer (priority 6) would also be running. Priority assignment
matters — wake word should be lower priority than stt_producer but
needs enough cycles to not miss activations.

**Resource budget:** the ESP32-S3-N16R8 has 8 MB PSRAM and ~320 KB
internal SRAM. The current pipeline uses ~5.3 MB PSRAM (5 MB ring +
TLS + helpers). Wake word model + buffers would need to fit in the
remaining ~2.7 MB PSRAM or internal SRAM depending on latency
requirements.

**State machine impact:** STATE_IDLE currently blocks on
`button_wait_press(100)` in a polling loop (also checks mode button).
Wake word would add a third check in that loop, or replace the button
check entirely.

---

## 7. Known stale documentation

These items are inaccurate relative to the current code and should be
updated as part of the next work in their respective repos.

| Document | Issue | Impact |
|---|---|---|
| `mic_capture/CURRENT_PROCESS.md` | Describes retired DeepSeek/ElevenLabs pipeline. Task topology (§1), subprocesses (§2), shared state (§3) all reference STATE_LLM, el_parse, el_send, deepseek.c, elevenlabs_ws.c. Actual chatbot.c has only STATE_IDLE and STATE_STT with ha_link_round_trip(). | Anyone reading CURRENT_PROCESS.md gets a completely wrong picture of the system |
| `mic_capture/CLAUDE.md §7` (deepgram_minimal contract) | Says the hot loop calls `stt_audio_helper_record_and_process()` directly. Actual code uses `stt_pipeline_pop()`. CURRENT_PROCESS.md row 9 flags this but the contract hasn't been updated. | The contract describes a dependency that doesn't exist in code |
| `mic_capture/CLAUDE.md §7` (post-send pacing) | Specifies `vTaskDelay(pdMS_TO_TICKS(64))` after each send. Actual code uses 128 ms timer-based cadence with 4-chunk batching. | Contract and code disagree on timing model |
| `mic_capture/CLAUDE.md §8` (TTS pipeline invariants) | Send-side rules 1-5 and 8 are marked as "retired" provenance but still physically present. `elevenlabs_ws.c` is still in the tree (Step 3 cleanup not done). | Noise in the contract; dead code in the repo |

---

## 8. Deployment topology

### Voice terminal firmware

Built with ESP-IDF 6.0 on the operator's workstation. Flashed via
USB (first time) or OTA. The operator builds and flashes manually;
CVC proposes diffs only.

### TTS server add-on

Lives at `esp_home_hub/addons/tts_server/`. Installed via
Supervisor's add-on repository mechanism (Settings → Add-on Store →
Repositories → add the GitHub URL). The repo must be public or
Supervisor can't clone.

**Critical deployment gotcha:** updating the repo + git pull on the
host does NOTHING. Supervisor has its own internal clone and only
re-pulls on Add-on Store → (three dot menu) → Reload. Then TTS Server →
Update (or Rebuild if no Update button). Stop/start re-runs the old
built image. Reload → Update/Rebuild is the only path.

---

## 9. Credential and secret boundaries

| Secret | Where it lives | Who holds it |
|---|---|---|
| Deepgram API key | `mic_capture/main/secrets.h` (gitignored) | Device firmware |
| WiFi credentials | `mic_capture/main/secrets.h` (gitignored) | Device firmware |
| Azure Speech key | Add-on config (HA UI) + `azure_secrets.py` (gitignored) for local dev | Add-on only |
| MQTT credentials | Add-on config (HA UI) | Add-on only |
| SUPERVISOR_TOKEN | Injected by HA Supervisor at container start | Add-on only |

The HA add-on link address (`ws://192.168.50.125:8766`) is
install-specific configuration, not a secret — hardcoded in
`ha_link.c` per CLAUDE.md §4 precedent.

No secret crosses a repo boundary in committed code. The voice
terminal firmware holds Deepgram and WiFi credentials; the add-on
holds Azure and MQTT credentials; HA Core holds Nest credentials.
Each boundary is enforced by gitignore.
