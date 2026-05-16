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
The trigger is an HA automation (e.g. a future Ring or motion integration on
the HA side); there is no physical button on this device in v1.

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

## Quality bar for v1

- **Latency:** buzzer responds within 1 second of the HA switch command being
  issued.
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
| Firmware framework | ESPHome (version pinned per-device in YAML `esphome:` block; specific version TBD when first YAML lands) |
| Host OS | Home Assistant OS (version recorded in `PROJECT_STATE.md` when first verified) |
| ESPHome add-on | Official `ESPHome` add-on running inside HA OS (version recorded in `PROJECT_STATE.md` when first verified) |
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
| Buzzer + | 23 | output | Active buzzer. Active-high: GPIO HIGH = beep, GPIO LOW = silent. Default LOW at boot (ESPHome `restore_mode: ALWAYS_OFF`). |

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
   multi-tone chimes, battery monitoring, OLED display, multiple buzzers).
2. Driving any GPIO not in the pin registry, or any GPIO listed as reserved.
3. Multi-file diffs without explicit operator approval.
4. Modifying any file under `archive/`.
5. Modifying any file outside this repo — including files on the HA host
   (`/config/`, `/share/`, add-on configs). CVC documents those changes for
   the operator to apply manually.
6. Logging or otherwise disclosing values from `secrets.yaml`, including
   partial disclosure.
7. Adding cloud services. The architecture is local-network only: ESP32 ↔ HA
   over the LAN. No external brokers, no vendor clouds.
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
