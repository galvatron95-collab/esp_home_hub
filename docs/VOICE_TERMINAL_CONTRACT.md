# VOICE TERMINAL CONTRACT

This file is the combined rulebook for the voice terminal system:
the ESP32-S3 firmware (`esp_ai_chat_bot/mic_capture`) and the HA
add-on (`esp_home_hub/addons/tts_server/`). It overrides Claude's
training and prior assumptions when they conflict.

The firmware side is strict — invariants earned from hardware
debugging and verified builds. The add-on side is descriptive —
it documents what the add-on does and the protocol it must honour,
without locking down Python implementation details. The cross-boundary
invariants (ring buffer, READY handshake, audio format, sequential
connections) are strict on both sides.

Last verified against code: 2026-06-11.

## Table of contents

0. System overview
1. Cross-boundary invariants (strict, both sides)
2. Firmware contract (strict, mic_capture)
3. Add-on description (loose, tts_server)
4. Governance and change handoff
5. Refusal list

---

# SECTION 0 — SYSTEM OVERVIEW

## What this system is

A voice assistant built on Home Assistant. The user presses a button
on an ESP32-S3 device, speaks, and hears a spoken response. The
device does not run an LLM and does not call any cloud TTS — it is a
voice terminal. Home Assistant is the brain.

```
Button press (GPIO 21, active low)
  │
  ▼
ESP32-S3 captures audio, streams to Deepgram (cloud STT)
  │
  ▼ finalised transcript
  │
ESP32 opens one LAN WebSocket to the HA add-on (:8766)
  sends JSON: {"text": "<transcript>", "lang": "en"|"zh"}
  │
  ▼
Add-on calls HA conversation/process API
  (conversation.claude_conversation — Anthropic integration)
  HA resolves intent (device control) or falls back to LLM
  │
  ▼ response text
  │
Add-on synthesises with Azure TTS (AvaMultilingualNeural)
  streams raw 16 kHz / 16-bit / mono PCM back over the same WS
  sends 0-byte binary frame = end of stream
  │
  ▼
ESP32 plays audio through speaker
  sends "READY" text frame when playback finishes
  connection closes, device returns to idle
```

One cloud service reached by the device: Deepgram. Everything else
is LAN-only. The add-on holds the Azure credentials and talks to HA;
the device never touches either directly.

## The two repos

| Repo | Contains |
|---|---|
| `esp_ai_chat_bot/mic_capture` | ESP32-S3 firmware (ESP-IDF 6.0, C) |
| `esp_home_hub` | HA add-on (`addons/tts_server/`, Python), plus cross-repo docs |

## Quality bar

- **Latency:** button press to start of spoken response ≤ 3 seconds
  (the portion the project controls — Deepgram cloud latency is
  external).
- **Reliability:** device survives WiFi outages and reconnects.
  HA link failures are logged and dropped; device returns to idle.
- **Audio quality:** intelligible speech. Robotic TTS is acceptable.

## Architectural invariants

These govern everything. If a rule in a later section contradicts
one of these, the invariant wins.

1. **Raw data is sacred.** The mic pipeline produces raw 32-bit PCM
   at 16 kHz, 2 channels. No module modifies it at the driver level.
2. **Always-on pipeline.** Once the audio pipeline initialises, it is
   never stopped, closed, reset, or reconfigured. "Stop recording"
   means "stop reading," not "stop hardware."
3. **Single ownership.** Only `bringup.c` owns codec handles.
4. **Service isolation.** Audio on Core 1. WiFi, UI, everything else
   on Core 0. Lock-free buffers between cores.
5. **Exclusive pin reservation.** Every GPIO is in the pin registry.
6. **Verified facts override documentation.** Boot logs and observed
   behaviour are ground truth.
7. **Physical presence required.** No code may drive unverified
   hardware.
8. **PSRAM OPI pins reserved.** GPIOs 35, 36, 37 are permanently
   off-limits on ESP32-S3R8.

## Pipeline process contracts

The chatbot is a chain of independent processes, each with its own
input/output contract:

- **STT**: audio frames in → finalised transcript out.
- **HA round-trip**: transcript out → audible speech in. The device
  sends the transcript and plays whatever PCM the add-on returns.
  Intent, LLM, and synthesis are HA's contracts, off-device.
- **Speaker**: PCM in → audible speech out. The playback links
  (`tts_ring`, `speaker_consumer`, `speaker`) are unchanged from the
  retired ElevenLabs pipeline and retain their invariants.

Each process owns its own bugs. A playback bug is not an STT problem;
a wrong response is HA's problem, not the device's.

## Translation pipeline

A mode button (GPIO 4) toggles Deepgram's input language between
English and Chinese. The `lang` field threads through:
Deepgram URL → ha_link JSON frame → add-on → `conversation/process`
API → HA conversation agent. Azure's multilingual voice speaks
whatever language the agent responds in.

## History

The device was originally a standalone chatbot with on-device DeepSeek
LLM and ElevenLabs cloud TTS. In May 2026, the operator pulled the
LLM and TTS out of firmware — HA became the brain. The retired
`deepseek.c` and `elevenlabs_ws.c` are still in the tree pending
cleanup.

---

# SECTION 1 — CROSS-BOUNDARY INVARIANTS

These invariants are depended on by code in both repos. Violating
them on either side breaks the system. **Strict on both sides.**

## 1.1 Ring buffer backpressure (tts_ring)

`tts_ring` (5 MB in PSRAM) is the single data path from
network-received PCM to the speaker.

**`tts_ring_write_blocking(src, len, wait_ticks)`**:
- **All-or-nothing.** Returns `len` on success, `0` on timeout.
  Never partial.
- **Release-before-wait.** Releases `s_mutex` before blocking on
  `s_space_avail`. Load-bearing — the consumer must drain while the
  producer blocks.

**`tts_ring_read(dst, max_len, wait_ticks)`**:
- Short-read OK. Returns bytes actually read; 0 on empty + timeout.

**SPSC by structural assumption.** One producer (WS event handler in
`ha_link.c`), one consumer (`speaker_consumer`). The sequential
connection model guarantees only one producer exists at a time.

**The add-on depends on this:** `tts_server.py` streams 4096-byte
chunks as fast as TCP allows. Within-stream pacing is handled entirely
by TCP backpressure propagating through the ESP32's WS library into
`tts_ring_write_blocking`. The add-on has no application-level flow
control beyond the READY handshake.

## 1.2 READY handshake protocol

Prevents between-stream overrun. Contract between add-on and device.

```
Server streams PCM → 4096-byte binary frames
Server sends 0-byte binary frame (EOS)
Server holds connection open, waiting
Client finishes playing all buffered audio
Client sends "READY" text frame
Server closes connection (port 8766)
  or accepts next request (port 8765)
```

**Within a stream:** TCP backpressure handles pacing.
**Between streams:** READY is the flow control.

On port 8766 (STT round-trip), the connection closes after one READY.

## 1.3 Audio format contract

PCM exchanged between add-on and device:

| Property | Value |
|---|---|
| Sample rate | 16,000 Hz |
| Bit depth | 16-bit signed |
| Channels | 1 (mono) |
| Byte order | Little-endian |
| Encoding | Raw PCM (no container, no header) |

The add-on requests `Raw16Khz16BitMonoPcm` from Azure. The device
converts 16-bit mono to 32-bit stereo for the ES8311 DAC.

Deepgram also receives 16 kHz / 16-bit / mono / linear16 from the
device.

## 1.4 Sequential connection model

The device never has more than one WebSocket open at a time:

1. Deepgram (TLS, cloud) — during STT capture
2. HA add-on (plain WS, LAN) — during transcript + response + playback
3. (none) — idle

Deepgram's socket is fully closed before the HA link opens. This
makes STT and TTS playback structurally non-overlapping on Core 1.

The ESP32-S3's mbedTLS cannot tolerate two concurrent TLS handshakes
(`PSA_ERROR_INVALID_SIGNATURE` confirmed on build `_021`).

## 1.5 JSON frame format

The device sends one text frame per connection on port 8766:

```json
{"text": "<transcript>", "lang": "en"}
```

`text`: the finalised Deepgram transcript. Currently built with
`snprintf` (no JSON escaping) — tracked technical debt, safe for
Nova-3 output.

`lang`: `"en"` or `"zh"`, from the mode button toggle. Passed through
to the HA conversation API's `language` parameter.

## 1.6 Speaker pipeline (always-on)

The speaker pipeline runs from boot to power-off:

```
tts_ring → speaker_consumer (Core 1, pri 5) → speaker_stream_write
           │                                    │
           │ 32 KB primer threshold             │ int16 mono → int32 stereo
           │ 20 ms ring-read wait               │ esp_codec_dev_write (ES8311)
           │ silence fill when empty            │
           │ 10 consecutive empties = done      │
           │                                    │
           └─ 90 ms I2S DMA depth (6 desc × 240 frames × 8 bytes)
              must be fed continuously or stale samples loop
```

`speaker_stream_finalise` writes 8 × 8192 zero bytes to flush DMA.

## 1.7 Core assignment

| Core | Responsibilities |
|---|---|
| Core 0 | WiFi, chatbot state machine, WS clients, display |
| Core 1 | Mic capture (stt_producer, pri 6), speaker (spk_cons, pri 5) |

The mic capture task must never block on a WiFi socket write.

---

# SECTION 2 — FIRMWARE CONTRACT (STRICT)

The ESP32-S3 firmware in `esp_ai_chat_bot/mic_capture` is governed by
`mic_capture/CLAUDE.md`, which is **authoritative** for every
firmware-specific rule, value, and refusal. This section does **not**
restate those rules — it orients the validator to what the firmware
contains and names the CLAUDE.md section that owns each topic. When a
firmware diff arrives, check it against `mic_capture/CLAUDE.md` (sent
as per-diff context), not against a summary here. Any concrete value
(GPIO number, buffer size, timeout, refusal number) lives in CLAUDE.md
and must not be duplicated in this document, where it could silently
drift.

## 2.1 Versions

| Item | Value |
|---|---|
| Target chip | ESP32-S3-N16R8 |
| Framework | ESP-IDF 6.0 |
| esp_codec_dev | 1.5.7 (exact) |
| PSRAM | 8 MB Octal at 80 MHz, enabled |

## 2.2 Pin registry

Authoritative pin assignments — audio subsystem, display, and reserved
GPIOs — are in `mic_capture/CLAUDE.md §3`. GPIOs 35/36/37 are PSRAM OPI
(Invariant 8) and permanently off-limits. Do not duplicate pin numbers
here.

## 2.3 Network

One cloud service reached by the device: **Deepgram** (STT, TLS).
One LAN peer: **HA add-on** (plain WS, port 8766).

No additional device-reached cloud service may be added.

Secrets, WiFi power-save lock, and the full network contract are in
`mic_capture/CLAUDE.md §4`. Key cross-boundary facts: the device
reaches exactly one cloud service (Deepgram); everything else is
LAN-only; no secret is ever logged or disclosed.

## 2.4 Bringup state machine

Authoritative in `mic_capture/CLAUDE.md §5`. The one cross-boundary
fact: the audio pipeline is always-on after bringup — no module stops,
resets, or reconfigures the codecs. The add-on can assume the speaker
is always ready to receive PCM.

## 2.5 Module contracts (summary)

**stt_audio_helper**: pure library, no tasks, no sockets. Reads
`mic_read()`, converts 32-bit stereo → 16-bit mono (>>8 bit-depth,
>>2 downmix). No DSP. Instance-safe (caller-owned struct).

**deepgram_minimal**: production STT consumer. Streams PCM to
Deepgram via `esp_websocket_client`. 128 ms cadence, 4-chunk batching
via `stt_pipeline_pop()`. Press-to-toggle with 60s hard cap.

**ha_link**: production transport. Opens WS to :8766, sends JSON
transcript, receives PCM via `tts_ring_write_blocking()` (1.5s
timeout), waits for EOS, sets `SPK_BIT_FINALISE`, waits
`SPK_BIT_DONE` (30s ceiling), sends READY, closes.

**chatbot**: state machine with STATE_IDLE and STATE_STT.
`deepgram_minimal_run()` then `ha_link_round_trip()`. Pinned to
Core 0, priority 5.

**speaker_consumer**: Core 1, priority 5. 32 KB primer, 20 ms
ring-read wait, silence fill, 5 MB ring init. Never stops.

**speaker**: `speaker_stream_write()` converts int16 mono → int32
stereo. Static chunk buffer. `speaker_stream_finalise()` flushes DMA.

The module summaries above orient the validator; the **authoritative**
per-module contracts (allowed/forbidden operations, exact buffer sizes,
immutability lists) are in `mic_capture/CLAUDE.md`: stt_audio_helper §6,
deepgram_minimal §7, audio pipeline integrity + TTS invariants §8,
codec wiring §2, bringup §5. The constants quoted above (1.5s, 30s,
32 KB, 5 MB) are convenience references only — `CLAUDE.md` owns their
authoritative values.

## 2.6 Audio pipeline integrity, TTS invariants, codec wiring

Authoritative in `mic_capture/CLAUDE.md §8` (audio integrity + TTS
receive/playback invariants + defensive-timer principle) and `§2`
(codec wiring). The receive/playback invariants survive the ElevenLabs
retirement because the HA downlink feeds the same `tts_ring` and
`speaker_consumer`. Cross-boundary-relevant facts that the add-on
depends on are stated in §1 of this document (ring backpressure, audio
format) — those are the strict, both-sides version; §8 of CLAUDE.md
owns the firmware-internal detail.

---

# SECTION 3 — ADD-ON DESCRIPTION (LOOSE)

The TTS server add-on (`esp_home_hub/addons/tts_server/`) bridges the
device and Home Assistant. This section describes what it does without
constraining how the Python code is structured. The **cross-boundary
invariants in §1 are strict** — the add-on must honour the READY
handshake, EOS marker, audio format, and JSON frame format regardless
of internal implementation.

## 3.1 Identity

| Property | Value |
|---|---|
| Name | TTS Server |
| Version | 0.4.0 |
| Slug | tts_server |
| Base image | python:3.11-slim |
| Architecture | amd64 only |

## 3.2 Ports

| Port | Purpose |
|---|---|
| 8765/tcp | MQTT-driven TTS broadcast (not currently used by voice terminal) |
| 8766/tcp | STT round-trip: transcript in, PCM out, one connection per request |

## 3.3 What port 8766 does

1. Receives JSON text frame from ESP32 (`{"text","lang"}`)
2. Fires `esp32_transcript` HA event (async, non-blocking)
3. Calls HA `conversation/process` API (blocking, up to 30s)
4. Synthesises response with Azure TTS (`Raw16Khz16BitMonoPcm`)
5. Streams PCM as 4096-byte binary frames
6. Sends 0-byte binary frame (EOS)
7. Waits for "READY" text frame
8. Closes connection

## 3.4 Configuration

Options set via HA add-on config UI:

| Option | Default | Purpose |
|---|---|---|
| `azure_key` | (empty) | Azure Speech subscription key |
| `azure_region` | (empty) | Azure region |
| `ha_conversation_agent` | conversation.claude_conversation | HA conversation agent |
| `azure_voice` | en-US-AvaMultilingualNeural | Azure TTS voice |
| `mqtt_host` | core-mosquitto | MQTT broker for port 8765 |
| `mqtt_port` | 1883 | MQTT port |
| `mqtt_username` | (empty) | MQTT auth |
| `mqtt_password` | (empty) | MQTT password |
| `mqtt_topic` | tts/response | MQTT topic |

## 3.5 HA API grants

- `hassio_api: true` — Supervisor API
- `homeassistant_api: true` — Core API (conversation/process, events)

Both required; `hassio_api` alone returns 401 on Core API calls.

## 3.6 Dependencies

`azure-cognitiveservices-speech` (requires glibc — Alpine fails),
`paho-mqtt`, `websockets`.

## 3.7 Deployment

Installed via Supervisor add-on repository. **Critical gotcha:**
updating the repo + git pull does nothing. Supervisor has its own
clone — must Reload in Add-on Store, then Update/Rebuild. Stop/start
re-runs the old image.

## 3.8 What is NOT constrained

The add-on's internal Python structure, error handling, logging, async
patterns, and Azure SDK usage are not governed by this contract. The
add-on is free to refactor internally as long as the §1 invariants
hold: correct audio format, EOS marker, READY handshake, and JSON
frame parsing.

---

# SECTION 4 — GOVERNANCE AND CHANGE HANDOFF

## 4.1 Roles

- **Operator (Rowan):** business analyst, does not write low-level
  code. All code flows through validation.
- **CVC (Claude Code):** proposes changes as unified diffs.
- **Validator (separate Claude instance):** reviews diffs against
  this contract.

## 4.2 Change handoff format

For every change, CVC produces:

1. One sentence of stated intent.
2. A unified diff (`--- a/file`, `+++ b/file`, `@@` hunks).
3. Nothing else.

Multi-file diffs require explicit operator approval before drafting.

## 4.3 Validator verdicts

- **APPROVE** — safe to apply
- **APPROVE WITH NOTE** — safe, CVC should know one thing
- **REJECT** — names the specific contract section violated
- **NEED MORE** — names exactly the one thing needed

## 4.4 PROJECT_STATE.md governance

Each repo has its own `PROJECT_STATE.md` with four sections: Working,
In progress, Blocked/deferred, Last verified build. CVC may only move
items between sections with operator verification. CVC reads it at
session start and quotes back In progress and Blocked before proposing
changes.

## 4.5 Credential boundaries

| Secret | Where | Who |
|---|---|---|
| Deepgram API key | `mic_capture/main/secrets.h` (gitignored) | Device |
| WiFi credentials | `mic_capture/main/secrets.h` (gitignored) | Device |
| Azure Speech key | Add-on config (HA UI) | Add-on |
| MQTT credentials | Add-on config (HA UI) | Add-on |
| SUPERVISOR_TOKEN | Injected by Supervisor | Add-on |

No secret crosses a repo boundary in committed code.

---

# SECTION 5 — REFUSAL LIST

CVC must refuse the following without explicit operator approval.
When refusing, name the specific item.

### Firmware refusals

The firmware refusal list is **authoritative in `mic_capture/CLAUDE.md
§16`** and is not restated here — restating it duplicated refusal
numbers and let them drift (this document's old #3 was CLAUDE.md's #13).
For any firmware diff, cite the refusal by its `mic_capture/CLAUDE.md
§16` number. The add-on side does not have firmware refusals.

### Cross-boundary refusals

These have no home in either repo's CLAUDE.md — they are the contract
of the black-box interface between the firmware and the add-on, and are
**authoritative here**. They bind both sides.

1. Changing the JSON frame format (`{"text","lang"}`), EOS marker
   (0-byte binary frame), or READY handshake without updating both
   sides.
2. Changing the audio format (16 kHz / 16-bit / mono) on one side
   without the other.
3. Opening a second WebSocket concurrently (violates the sequential
   model).

### Add-on refusals

Authoritative here (the add-on has no CLAUDE.md).

4. Hardcoding credentials in committed code.
5. Removing the `homeassistant_api: true` grant (breaks Core API).

---

# COMPANION DOCUMENTS

- **`mic_capture/CLAUDE.md`** — the full detailed firmware contract
  (authoritative for firmware-specific rules not summarised here)
- **`esp_home_hub/docs/SYSTEM_ARCHITECTURE.md`** — detailed cross-repo
  architecture with data flow diagrams
- **`esp_home_hub/docs/VALIDATOR_INTRODUCTION.md`** — session-starter
  briefing for the validator
- **`mic_capture/PROJECT_STATE.md`** — firmware current state
- **`esp_home_hub/PROJECT_STATE.md`** — add-on / doorbell current state

---

# END OF VOICE_TERMINAL_CONTRACT.md
