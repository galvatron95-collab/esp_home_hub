# CLAUDE PROJECT CONTRACT — DOORBELL ESPHOME DEVICE

This file is the rulebook for code generation, refactoring, and updates to this
project. It overrides Claude's training and prior assumptions when they
conflict. Rules earn their place by preventing real bugs; this file is
intentionally short and grows by example, not by anticipation.

## Table of contents

0. Project overview
1. Versions and dependencies
2. Pin registry
3. Network and secrets
4. Deployment workflow
5. Module contracts
6. PROJECT_STATE.md governance
7. Change handoff format
8. Refusal list

---

# SECTION 0 — PROJECT OVERVIEW

## What this project is

A doorbell device built on an ESP32 running ESPHome, controlled by a self-hosted
Home Assistant OS instance on the LAN. The ESP32 exposes the buzzer as a
`switch` entity. When HA commands the switch on, a GPIO drives an active buzzer
HIGH and it beeps; when commanded off, GPIO goes LOW and the buzzer is silent.
The trigger is an HA automation that fires on doorbell events from a Google
Nest Doorbell (wired, 2nd gen). Events reach HA via the official HA Nest
integration, which subscribes to Google's Cloud Pub/Sub. There is no physical
button on the ESP32 device in v1.

The Nest event path is read-only and inbound only (Google → HA → ESP32). The
ESP32-to-HA command path remains LAN-only. See §8 refusal #7 for the precise
carve-out wording.

The earlier esp-matter / Google Home design is retired and lives under
`archive/`. See `archive/README.md` for why.

## What v1 is

- Single hardware behaviour: buzzer on / buzzer off, driven by an HA-issued
  switch command over the local network.
- Single ESPHome `switch` entity, adopted once into HA, persistent across
  reboots.
- Reconnects to WiFi and to the HA API automatically after outages.
- Original ESP32 (not S3): no PSRAM, no octal flash, single I2S peripheral,
  240 MHz dual-core.

## What v1 is not

V1 does not include sensors, button input on the device, multi-tone chimes,
battery monitoring, OLED display, or any of the other components in the kit.
OTA is provided by ESPHome's built-in mechanism but is not a v1 feature target
— it is a property of the toolchain. Those deferred items exist in the kit;
v1 ignores them.

v1 originally used an active buzzer on GPIO 23. The operator observed in
daily use that the buzzer is unpleasantly loud with no volume control —
a real quality issue, not aesthetics. The v1 hardware is being swapped
from active buzzer to passive piezo on the same pin, driven by ESPHome's
`rtttl` output for a softer chime. See §8 refusal #1 for the precise
carve-out wording.

A DHT11 temperature/humidity sensor is also being added to the doorbell
device on GPIO 4. Use case is dashboard visibility only — no automations,
no thresholds, no alerts — explicitly framed as a learning step ahead of
the larger multi-device sensor build-out the operator has indicated. The
DHT11's accuracy (±5% RH, ±2°C) is sufficient for dashboard visibility
but insufficient for serious data work; any future use case requiring
real precision should reach for a BME280 or similar, not extend the
DHT11. See §8 refusal #1 for the precise carve-out wording.

An LM393 digital-output LDR (light-dependent resistor) module is also
being added to the doorbell device on GPIO 21. Use case is data
gathering — instrument the doorbell location's ambient light to learn
what range of values shows up over time, before designing any
light-driven automations that would need threshold knowledge to be
useful. The digital output gives binary dark/not-dark transitions
(threshold set by the module's onboard trim pot), not absolute lux
readings. This is the fourth refusal #1 carve-out in the project and
the framing is honestly weaker than past admissions (no concrete
downstream behaviour at admission time), so the carve-out includes a
reconsider point: if no concrete automation use case has emerged from
the data within 90 days of admission (by 2026-08-15), the sensor's
contract place is reconsidered. The reconsider doesn't force removal —
just a check-in. See §8 refusal #1 for the precise carve-out wording.

## Quality bar for v1

- **Latency, command path (ESP32 ↔ HA, LAN):** buzzer responds within 1
  second of HA issuing the `switch.buzzer` ON command. This is the
  latency the project controls.
- **Latency, trigger ingress (Google Nest event → HA):** bounded by
  upstream Google Pub/Sub delivery; observed ~8-10 seconds end-to-end
  from doorbell press to buzzer beep. This is not a project-controlled
  latency and is not a quality-bar miss — the cloud path is the cost of
  refusal #7's carve-out. See the latency paragraph in §5's "Google Nest
  event ingress" module contract for the reasoning.
- **Reliability:** the device survives WiFi outages — when WiFi returns, the
  ESPHome native API reconnects to HA without re-adoption.
- **Persistence:** HA adoption survives power cycles on both ends.
  Re-adoption is a setup step, not a runtime event.

## Architectural invariants

1. **Single ownership of hardware handles.** Each piece of hardware (GPIO,
   WiFi, the ESPHome API client) is owned by exactly one ESPHome component.
   No custom code reaches around an ESPHome-owned handle.
2. **Fail loudly on init, log-and-continue if recoverable at runtime.** Init
   failures are fatal: ESPHome's default behaviour (reboot on failed setup) is
   not overridden. Runtime failures (WiFi drop, HA API disconnect) log at
   WARN/ERROR and the responsible component recovers in the background.
3. **Verified facts override documentation.** Boot logs, datasheets, and
   observed behaviour are ground truth. Vendor docs, product listings, and
   prior assumptions are hypotheses.
4. **Physical presence required.** No YAML may drive a GPIO or command a
   peripheral that is not physically wired and verified on the current board.
   Components in the kit but not wired in v1 must not be referenced.

## Source-of-truth files

- **`CLAUDE.md`** (this file): the rulebook. Changes rarely.
- **`PROJECT_STATE.md`**: the logbook of current state. Changes constantly.
  Updated through the governance process in §6.
- **`docs/modules/<modulename>.md`**: per-module documentation as modules land.
  Authoritative description of what each module does. CLAUDE.md remains
  authoritative for cross-module rules.
- **`archive/`**: frozen historical design (esp-matter era). Never modified.

## Project relationship

This project is standalone. There is no shared runtime state, no shared
codebase, and no shared repo with any other project. The HA instance, the
ESPHome firmware, and the YAML in this repo together constitute the project.

---

# SECTION 1 — VERSIONS AND DEPENDENCIES

Pinned. Versions below are authoritative. Bumping any value requires a
CLAUDE.md diff.

| Item | Value |
|---|---|
| Target chip | ESP32 (original, not S3) |
| Firmware framework | ESPHome 2026.4.5 (first verified 2026-05-16; floor enforced per-device via YAML `min_version`, currently `2025.4.0`) |
| Host OS | Home Assistant OS 17.3 (first verified 2026-05-16) |
| ESPHome add-on | Official `ESPHome` add-on, version 2026.4.5 (first verified 2026-05-16) |
| HA API encryption | Required — every device YAML must include an `api:` block with an `encryption: key:` value sourced from `esphome/secrets.yaml` |

---

# SECTION 2 — PIN REGISTRY

This registry is authoritative. Every GPIO used by this project is listed here.
GPIOs not in this table are unassigned. Assigning a new GPIO requires updating
this table in the same diff that uses it.

| Function | GPIO | Direction | Notes |
|---|---|---|---|
| Boot button | 0 | input | Strap pin, reserved. Do not assign. |
| UART0 TX | 1 | output | Default serial console. Reserved. |
| UART0 RX | 3 | input | Default serial console. Reserved. |
| SPI flash | 6–11 | — | Internal flash bus. Do not use. |
| DHT11 DATA | 32 | input | Single-wire data line to DHT11 temp/humidity sensor. Internal pull-up enabled by ESPHome's `dht` component. (Moved from GPIO 4 during bring-up debugging; the underlying issue was wiring, not pin choice, but GPIO 32 was the pin in place once the sensor started reporting.) |
| LDR DO | 21 | input | Digital output line from LM393 LDR module. HIGH = light above the module's trim-pot threshold; LOW = below. (Was admitted on GPIO 13 in the scope diff; operator landed on GPIO 21 at wiring time for breadboard convenience.) |
| Audio output + | 23 | output | Passive piezo, driven by ESPHome `rtttl` output. Idle LOW. Replaces the original active buzzer; same pin, same direction, different drive pattern. Active buzzer wiring (GPIO HIGH = beep) no longer applies. |

---

# SECTION 3 — NETWORK AND SECRETS

## WiFi

- Mode: STA
- Auth: WPA2-PSK
- Power save: disabled (`wifi:` block must set `power_save_mode: NONE`). Must
  not be removed or weakened.
- Reconnect: ESPHome's `wifi:` component handles reconnect automatically; the
  HA API client reconnects when WiFi returns.

## secrets.yaml schema

ESPHome convention: a flat YAML map at `esphome/secrets.yaml`, referenced from
device YAMLs with `!secret <name>`. Required keys for v1:

```yaml
wifi_ssid: "..."
wifi_password: "..."
api_encryption_key: "..."   # base64, generated per ESPHome docs
ota_password: "..."         # used by ESPHome's built-in OTA component
```

Additional keys are added as device YAMLs require them.

## Google Cloud credentials (Nest integration)

The HA Nest integration requires Google Cloud OAuth client credentials and a
Pub/Sub subscription. These credentials live inside HA's configuration store
(not in this repo) and are entered into HA's UI by the operator during
integration adoption. They are not committed, not logged, and not referenced
from any YAML in this repo. CVC does not see these values at any point;
per refusal #5, CVC documents the setup steps but does not execute them.

## Secrets rules

1. `esphome/secrets.yaml` is listed in `.gitignore` and is never committed.
   A redacted example lives at `esphome/secrets.yaml.example`.
2. No build artifact, log line, commit message, or diff embeds the contents of
   `secrets.yaml`. This includes partial disclosure (length, prefix,
   redacted-but-shaped placeholders).
3. Only device YAMLs that consume a secret may reference it via `!secret`.
   Documentation files (including this one) do not contain example values that
   resemble real secrets.

---

# SECTION 4 — DEPLOYMENT WORKFLOW

Canonical deploy path. Each step has exactly one owner.

1. **CVC writes / edits device YAML** under `esphome/` on the operator's
   workstation. Diff goes through §7.
2. **Operator commits and pushes** to the project's git remote (GitHub).
3. **Operator pulls on the HA host.** The HA-side working copy lives at
   `/config/esphome/` and is a git clone of this repo's `esphome/` subdirectory
   (initial clone is a one-time setup step; subsequent updates are `git pull`).
   The pull is triggered manually by the operator from the Studio Code Server
   or SSH add-on running inside HA.
4. **Operator clicks Install** in the ESPHome dashboard (HA UI). The add-on
   compiles the YAML and pushes firmware to the device over OTA (or USB on
   first flash, before OTA is reachable).
5. **Operator confirms behaviour** against the v1 quality bar (§0).

CVC owns steps 1 and any documentation of steps 3 and 4. CVC does not perform
steps 2, 3, 4, or 5. CVC must not assume a step has happened without operator
confirmation.

The one-time HA-side setup (installing the ESPHome add-on, installing the SSH
or Studio Code Server add-on, cloning the repo to `/config/esphome/`) is
documented in `docs/HA_SETUP.md` as that document lands. Until then, those
steps are operator tribal knowledge and CVC may ask about them.

---

# SECTION 5 — MODULE CONTRACTS

Empty. Module contracts are added per device YAML as devices land. Each
contract covers: purpose, entities exposed to HA, GPIO usage, allowed/forbidden
operations.

## Module: Audio output (doorbell chime)

**Purpose.** Produce the audible doorbell chime when HA's DoorBuzzer
automation triggers it. Replaces the original active-buzzer `switch.buzzer`
per refusal #1's narrow carve-out.

**Defined in.** `esphome/doorbell-buzzer.yaml`.

**Hardware.** Passive piezo on GPIO 23 (CLAUDE.md §2). Driven by an
`output: ledc` PWM channel which `rtttl:` uses as its tone source.

**Entity exposed to HA.** A single `button` entity, name "Play Chime"
(default object id `button.doorbell_buzzer_play_chime` — HA composes the
id from device name + entity name; operator-confirmed at adoption).
Confirmed at adoption 2026-05-16: `button.doorbell_buzzer_play_chime`.
Pressing the button plays one chime; the button has no state.

**Chime.** A single RTTTL string compiled into the firmware:
`Ding:d=4,o=7,b=180:e,c` — two notes (E7, C7) at 180 bpm, ~700 ms total.
Octave 7 (rather than the original octave 5) was chosen to bring the
notes closer to the passive piezo's resonant frequency, which made the
chime audibly louder for the operator's mounting environment.
This is the v1 chime. Changing the RTTTL string is a YAML edit + flash,
not a contract change. Adding a *second distinct* chime requires a
refusal #1 amendment per its current wording ("single chime melody").

**Allowed operations.** Press the button via the HA native API. Edit the
RTTTL string in YAML and reflash.

**Forbidden operations.** Driving GPIO 23 as a plain digital output
(would conflict with the ledc PWM and stop the rtttl component working).
Exposing the piezo as a `switch` entity (would re-introduce the old
active-buzzer shape; the carve-out is for `rtttl`-driven chime, not
arbitrary tones). Adding a second `button` or `switch` for an alternate
chime (multiple-chime scope drift).

**HA-side automation transition.** The DoorBuzzer automation currently
runs as five "Press button" actions targeting
`button.doorbell_buzzer_play_chime` with a 1-second wait between each,
producing ~5 seconds of repeated chimes per doorbell trigger. The
5×1s shape was chosen after testing because a single press (~700 ms)
is too short to reliably register as "the doorbell rang." Changing the
press count or wait time is an HA-UI edit; the contract value here is
the authoritative source — drift between the two should resolve by
updating this section, not by leaving them out of sync. The earlier
pre-rtttl automation (Turn on `switch.buzzer` → Wait → Turn off) is
retired; `switch.buzzer` no longer exists.

## Module: Environmental sensor (DHT11)

**Purpose.** Expose ambient temperature and humidity at the doorbell
location to HA's dashboard. Dashboard-visibility-only per refusal #1's
narrow carve-out — no automations, no thresholds, no alerts.

**Defined in.** `esphome/doorbell-buzzer.yaml`.

**Hardware.** DHT11 single-wire temp/humidity sensor on GPIO 32
(CLAUDE.md §2). VCC and GND to the ESP32's 3.3V and GND rails.

**Entities exposed to HA.** Two `sensor` entities, names "Temperature"
(default object id `sensor.doorbell_buzzer_temperature`) and "Humidity"
(default object id `sensor.doorbell_buzzer_humidity`). Operator-confirmed
at adoption.

**Update interval.** 60 seconds. Matches the ESPHome `dht` component's
default; sufficient for ambient-environment changes (which don't move
meaningfully on shorter timescales) and gentle on HA's recorder.

**Precision.** Both entities report to integer precision
(`accuracy_decimals: 0` plus a `round: 0` filter) to match the DHT11's
actual ±2°C / ±5% RH accuracy. The component's default 0.1° / 0.1% RH
display implies precision the sensor doesn't have.

**Allowed operations.** Display the readings in HA dashboards and history
graphs. Adjust the update interval in YAML.

**Forbidden operations.** Treating the readings as precise (e.g. driving
automations off ±0.5°C thresholds, comparing values between two DHT11s
to detect drift) — the DHT11's accuracy doesn't support those uses.
If precision-requiring use cases emerge, replace the sensor with a
BME280 or similar per §0; do not extend the DHT11 with software
calibration. Adding a second sensor of any type on the doorbell device
(scope drift past refusal #1's "one DHT11" admission).

## Module: Light sensor (LM393 LDR)

**Purpose.** Surface a binary ambient-light reading at the doorbell
location to HA, for data gathering ahead of any future light-driven
automation. Per refusal #1's narrow carve-out, this entry exists
under a 90-day reconsider point: by 2026-08-15, if no concrete
automation use case has emerged from the data, the sensor's contract
place is reconsidered.

**Defined in.** `esphome/doorbell-buzzer.yaml`.

**Hardware.** LM393 comparator-based LDR module on GPIO 21
(CLAUDE.md §2). VCC and GND to the ESP32's 3.3V/GND rails. The
module's onboard trim-pot sets the dark/not-dark threshold; the DO
output reflects whether ambient light is above or below it.

**Entity exposed to HA.** A single `binary_sensor` entity, name
"Light" (default object id `binary_sensor.doorbell_buzzer_light`).
Operator-confirmed at adoption.

**Polarity.** Module convention is `on` = light above threshold,
`off` = below. The operator's physical module is inverted relative
to that convention (reports `on` when dark), corrected by
`inverted: true` on the YAML pin block. The contract value remains
"above threshold = on" regardless of which YAML setting achieves it.

**Allowed operations.** Read the binary state in HA dashboards. Tune
the threshold by physically turning the module's trim-pot. Add or
remove `inverted: true` on the YAML pin block to match physical
module polarity to the contract convention above.

**Forbidden operations.** Treating the binary signal as a calibrated
light measurement (it is not lux; it is threshold-relative). Comparing
readings between two LM393 modules without acknowledging that each
module's pot setting is independent. Driving any automation off the
signal before the data-gathering phase has produced a concrete use
case (which is the entire point of the 90-day reconsider). If a
precision-requiring use case emerges, replace the LM393 with a
BH1750 (calibrated lux) or analog LDR per §0; do not extend the
LM393.

## Module: Google Nest event ingress

**Purpose.** Surface doorbell-press events from the Google Nest Doorbell
GWX3T into Home Assistant so an HA automation can drive `switch.buzzer`
(the ESPHome device defined in `esphome/doorbell-buzzer.yaml`).

**Source.** The HA Nest integration (Google Device Access + Cloud Pub/Sub),
adopted into HA per `PROJECT_STATE.md` "Last verified deploy". Permitted
under §8 refusal #7's narrow carve-out; see that refusal for scope.

**Events consumed.** One `nest_event` type reaches this project's
automation: `doorbell_chime` — physical button press at the doorbell.

**Events ignored in v1.** `camera_person`, `camera_motion`, `camera_sound`,
and any other `nest_event` type the integration may surface. `camera_person`
was considered as a second trigger during v1 design but dropped before the
automation landed — its noise profile (fires for delivery drivers, passing
pedestrians) made the v1 use case worse, not better. Adding it back later
is a contract amendment, not a bug.

**HA device.** The Nest doorbell registers in HA as a single device with an
HA-internal device id, referred to here as `<NEST_DOORBELL_DEVICE_ID>`.
Automations filter on this id to avoid triggering on any future Nest device
added to the carve-out. The operator recovers the actual id from HA's
Devices & services UI when wiring the automation; it is not committed to
this repo because it is install-specific (changes on HA rebuild/restore)
and because it characterises the home install in the §3-spirit sense.

**Automation contract.** A single HA automation (lives in HA's Automations
UI, not in this repo, per refusal #5) fires on the consumed event from
the device above, sets `switch.buzzer` ON, waits 5 seconds, sets it OFF.
No debounce, no cooldown, no notification side-effects in v1.

**Latency expectation.** End-to-end (doorbell button press → buzzer beep)
is bounded by Google Pub/Sub delivery time, observed at ~8-10 seconds
during first-verify. This exceeds the §0 command-path latency bar of 1
second by design — see §0's separated ingress-latency clause. The 1-second
bar applies only after HA issues the switch command; it does not apply to
the cloud event hop. Future-CVC should not treat this latency as a bug to
fix at the ESPHome or HA-automation layer; the latency is inherent to
Google's cloud and is the cost of refusal #7's carve-out.

---

# SECTION 6 — PROJECT_STATE.md GOVERNANCE

`PROJECT_STATE.md` contains exactly four sections, in order: **Working**,
**In progress**, **Blocked / deferred**, **Last verified deploy**. CVC may
move items between these sections only when the operator has explicitly
verified the move. CVC may not promote an item to "Working" on its own
authority — verification is a human act.

Diffs to `PROJECT_STATE.md` go through the change handoff format like any
other change. CVC reads `PROJECT_STATE.md` at the start of every session and
quotes back the "In progress" and "Blocked / deferred" sections before
proposing any change.

History lives in git. The file contains only the current state.

---

# SECTION 7 — CHANGE HANDOFF FORMAT

For every change, CVC produces:

1. One sentence of stated intent (plain language, what the change does).
2. A unified diff (`--- a/file`, `+++ b/file`, `@@` hunks, standard format).
3. Nothing else.

The operator relays the intent and diff to a separate validator chat. The
validator returns one of four verdicts plus one sentence of reasoning:

- **APPROVE** — safe to apply
- **APPROVE WITH NOTE** — safe, but CVC should know one thing
- **REJECT** — do not apply; names the specific `CLAUDE.md` section violated
- **NEED MORE** — verdict cannot be decided from the diff alone; names
  exactly the one thing needed

Single-file diffs are the default. Multi-file diffs require explicit operator
approval before drafting. CVC operating details are in
`docs/CVC_INSTRUCTIONS.md`; validator operating details are in
`docs/VALIDATOR_INSTRUCTIONS.md`.

## Pre-diff sanity check

Before drafting any diff that creates a new file, CVC must first verify the
file does not already exist (e.g. via `git ls-files <path>` or `cat <path>`).
A "create new file" diff whose target is already tracked is a wrong-shape diff
and must be withdrawn rather than applied. The check is cheap; the cost of a
wrong-shape diff is wasted validator review and potential history confusion.

---

# SECTION 8 — REFUSAL LIST

CVC must refuse the following without explicit operator approval in the
current session. Refusing is the correct response — when refusing, name the
specific item and ask whether to proceed.

1. Adding features beyond v1 scope (sensors, on-device button input,
   multi-tone audio beyond a single doorbell-chime output, battery
   monitoring, OLED display, multiple audio outputs). Two sensors are
   admitted on the doorbell-buzzer device as narrow exceptions, each
   with §0 motivation: a DHT11 temp/humidity sensor on GPIO 32 (was
   admitted on GPIO 4; see commit history), and an LM393 digital-output
   LDR on GPIO 13 (instrument-to-inform-future-automation framing with
   a 90-day reconsider point — see §0). All other sensors (PIR, mmWave,
   analog LDR / BH1750, obstacle avoidance, air quality, etc.) remain
   refused; adding more is a fresh refusal #1 amendment per the
   cumulative-drift discipline. The v1 audio output
   is a single passive piezo on GPIO 23 driven by ESPHome's `rtttl`
   output, replacing the original active buzzer. A single chime melody
   (mono, RTTTL-formatted) is in-scope; multiple distinct chimes,
   sampled audio, or any second audio output requires a fresh refusal
   #1 amendment.
2. Driving any GPIO not in the pin registry, or any GPIO listed as reserved.
3. Multi-file diffs without explicit operator approval.
4. Modifying any file under `archive/`.
5. Modifying any file outside this repo — including files on the HA host
   (`/config/`, `/share/`, add-on configs). CVC documents those changes for
   the operator to apply manually.
6. Logging or otherwise disclosing values from `secrets.yaml`, including
   partial disclosure.
7. Adding cloud services beyond the single Google Nest event ingress (HA Nest
   integration via Google Device Access and Cloud Pub/Sub, for the Nest
   Doorbell GWX3T). The architecture is local-network-only for command paths:
   ESP32 ↔ HA over the LAN. No external brokers in the command path, no
   vendor clouds for device control. The Nest event ingress is read-only and
   inbound only — events flow Google → HA; no HA → Google traffic is
   permitted as part of this carve-out.
8. Updating `PROJECT_STATE.md` outside the §6 governance process.
9. Declaring something "working" in `PROJECT_STATE.md` without explicit
   operator verification.
10. Removing or weakening the `power_save_mode: NONE` setting in any device
    `wifi:` block.
11. Removing or weakening API encryption (every device must keep the `api:`
    block with `encryption:`).
12. Adding defensive timers or watchdogs beyond ESPHome defaults without
    justifying the specific physical or network failure mode the timer
    catches and demonstrating no other layer addresses it.

---

# END OF CLAUDE.md
