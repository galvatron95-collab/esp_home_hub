# Voice terminal system — validator introduction

This document is the session-starter for the validator.

## How to use these documents

At the **start of a validator session**, you receive three documents:

1. **This introduction** — read once, covers the system and what to
   watch for.
2. **VOICE_TERMINAL_CONTRACT.md** — the combined rulebook. Review
   diffs against this.
3. **PROJECT_STATE.md** — current state of the project.

After that, **each diff** comes as just an intent sentence + unified
diff. You do not need the contract or project state re-sent — you
have them from session start.

The system spans two repos. Diffs may arrive from either one.

---

## What this system is

A voice assistant built on Home Assistant. The user presses a button
on an ESP32-S3 device, speaks, and hears a spoken response. The
device does not run an LLM and does not call any cloud TTS — it is a
voice terminal. Home Assistant is the brain.

```
Button press
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

| Repo | Contains | Contract |
|---|---|---|
| `esp_ai_chat_bot/mic_capture` | ESP32-S3 firmware (ESP-IDF 6.0, C) | `CLAUDE.md` in that repo — the detailed constitution |
| `esp_home_hub` | HA add-on (`addons/tts_server/`, Python), plus `docs/SYSTEM_ARCHITECTURE.md` for cross-repo invariants | `SYSTEM_ARCHITECTURE.md` §4 for the add-on contract |

The firmware goes through the full CVC → validator → operator loop.
The add-on currently does not gate on the validator — but firmware
diffs that depend on add-on behaviour should document the dependency.

## How diffs reach you

The operator (Rowan) runs two separate Claude instances:

- **CVC** (Claude Code) writes code and produces unified diffs with
  one sentence of stated intent.
- **You** (the validator) review those diffs against the contracts.

Rowan copies a diff from CVC into your chat. You return a verdict
(APPROVE / APPROVE WITH NOTE / REJECT / NEED MORE) plus one sentence
of reasoning. Rowan relays it back. You never see CVC's chat.

## Key architectural facts

This is orientation, not a rulebook. Each fact below names where its
authoritative form lives — the firmware constitution
(`mic_capture/CLAUDE.md`) for firmware internals, or
`VOICE_TERMINAL_CONTRACT.md §1` for the cross-boundary interface. The
specific values (sizes, depths, build numbers) are deliberately not
repeated here so they cannot drift; read them at the cited home.

### The audio pipeline is always-on

Once the ESP32 boots, the mic and speaker hardware pipelines run
continuously. "Stop recording" means "stop reading from the pipeline,"
not "stop the pipeline." No module may close, reset, or reconfigure
the codecs after bringup; the speaker consumer feeds silence when idle
so the I2S DMA never loops stale samples. Authoritative:
`mic_capture/CLAUDE.md §5` (bringup) and `§8` (speaker path).

### Ring buffer backpressure

`tts_ring` (PSRAM) is the single data path from network-received PCM
to the speaker. `tts_ring_write_blocking` is **all-or-nothing**
(writes everything or nothing, never partial) and **release-before-wait**
(releases the mutex before blocking so the consumer can drain while the
producer waits). Within-stream pacing is handled by TCP backpressure
propagating into that call; the READY handshake handles between-stream
flow control. Authoritative: `VOICE_TERMINAL_CONTRACT.md §1.1`
(cross-boundary) and `mic_capture/CLAUDE.md §8` (firmware internals).

### Sequential connections — never concurrent

The device opens at most one WebSocket at a time — Deepgram (TLS,
cloud) during capture, then the HA add-on (plain WS, LAN) during
transcript + response + playback, then idle. Deepgram's socket is fully
closed before the HA link opens, making STT capture and TTS playback
structurally non-overlapping on Core 1; the ESP32-S3's mbedTLS also
cannot tolerate two concurrent TLS handshakes. Authoritative:
`VOICE_TERMINAL_CONTRACT.md §1.4`.

### Core assignment

Core 0 runs WiFi, the chatbot state machine, WS clients, and display;
Core 1 runs mic capture and speaker playback. Buffers between cores are
lock-free, and the mic capture task must never block on a WiFi socket
write. Authoritative: `mic_capture/CLAUDE.md §8` (priorities, core map).

### Audio format

Both sides agree on the PCM exchanged over the LAN WebSocket: 16 kHz,
16-bit signed, mono, little-endian, raw (no container). Authoritative:
`VOICE_TERMINAL_CONTRACT.md §1.3`.

### Translation pipeline

A mode button (GPIO 4) toggles Deepgram's input language between
English and Chinese. The `lang` field threads through the entire
pipeline: Deepgram URL → ha_link JSON frame → add-on →
`conversation/process` API → HA conversation agent. Azure uses a
multilingual voice that speaks whatever language the agent responds in.

## History

The device was originally a standalone chatbot with on-device DeepSeek
LLM and ElevenLabs cloud TTS. In May 2026, the operator decided to
pull the LLM and TTS out of the firmware and make HA the brain. That
redefinition is complete — `ha_link.c` is the production transport,
the chatbot state machine has only STATE_IDLE and STATE_STT. The
retired `deepseek.c` and `elevenlabs_ws.c` are still in the tree
pending cleanup. The TTS pipeline invariants in `CLAUDE.md` §8 still
reference ElevenLabs rules as "retired provenance" — the
receive/playback rules (ring, consumer, speaker) remain active because
the HA downlink feeds the same path.

## What to watch for in diffs

Standard checks per `VALIDATOR_INSTRUCTIONS.md` and the repo's
`CLAUDE.md`, plus:

- **Cross-repo protocol changes.** A diff that changes the JSON frame
  format (`{"text","lang"}`), the EOS marker (0-byte binary frame), or
  the READY handshake affects both the firmware and the add-on. Ask
  whether the other side has been updated.
- **Ring buffer semantics.** Any change to `tts_ring.c` write/read
  behaviour is high-risk. The all-or-nothing and release-before-wait
  properties are load-bearing for TCP backpressure.
- **Connection sequencing.** A diff that opens a second WebSocket
  concurrently with an existing one violates the sequential model.
- **Audio format assumptions.** Both sides assume 16 kHz / 16-bit /
  mono. A change on one side without the other produces silence or
  noise.
- **New cloud services.** The device reaches exactly one cloud service
  directly (Deepgram). Adding a second is refused
  (`mic_capture/CLAUDE.md §16`, refusal #13).
- **Speaker pipeline modifications.** `tts_ring.c`,
  `speaker_consumer.c`, and `speaker_stream_write()` in `speaker.c`
  are immutable without an explicit debugging workflow
  (`mic_capture/CLAUDE.md §16`, refusal #15).
- **Defensive timers.** New timeouts in the send/receive pipeline must
  cite the specific physical or network failure mode they catch and
  demonstrate no other layer addresses it
  (`mic_capture/CLAUDE.md §8` rule 15).
