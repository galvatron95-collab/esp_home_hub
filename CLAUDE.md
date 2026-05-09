# CLAUDE PROJECT CONTRACT — DOORBELL MATTER DEVICE

This file is the rulebook for code generation, refactoring, and updates to this project. It overrides Claude's training and prior assumptions when they conflict. Rules earn their place by preventing real bugs; this file is intentionally short and grows by example, not by anticipation.

## Table of contents

0. Project overview
1. Versions and dependencies
2. Pin registry
3. Network and secrets
4. Bringup
5. Module contracts
6. PROJECT_STATE.md governance
7. Change handoff format
8. Refusal list

---

# SECTION 0 — PROJECT OVERVIEW

## What this project is

A Matter-over-WiFi doorbell device. The ESP32 exposes a single Matter endpoint (On/Off Light or On/Off Switch) to Google Home. When Google Home commands the device on, the ESP32 drives a GPIO HIGH and an active buzzer beeps. When Google Home commands off, the GPIO goes LOW and the buzzer is silent. The trigger comes from Google Home automation (e.g. Ring PersonDetection → command the buzzer on); there is no physical button on this device in v1.

## What v1 is

- Single hardware behaviour: buzzer on / buzzer off, driven by a Matter On/Off command.
- Single Matter endpoint, commissioned once into Google Home, persistent across reboots.
- Reconnects to WiFi automatically after outages.
- Original ESP32 (not S3): no PSRAM, no octal flash, single I2S peripheral, 240 MHz dual-core.

## What v1 is not

V1 does not include sensors, button input on the device, multi-tone chimes, OTA updates, battery monitoring, OLED display, or any of the other components in the kit. Those are deferred. The kit hardware exists; v1 ignores it.

## Quality bar for v1

- **Latency:** buzzer responds within 1 second of the Google Home command being issued.
- **Reliability:** the device survives WiFi outages — when WiFi returns, Matter resumes without re-commissioning.
- **Persistence:** Matter commissioning survives power cycles. Re-commissioning is a setup step, not a runtime event.

## Architectural invariants

1. **Single ownership of hardware handles.** Each piece of hardware (GPIO, WiFi, Matter stack) is initialised and owned by exactly one module. No other module opens, closes, or reconfigures it.
2. **Fail loudly on init, log-and-continue if recoverable at runtime.** Init failures are fatal: the device logs the failure and reboots or halts. Runtime failures (WiFi drop, Matter session loss) log at WARN/ERROR and the responsible module recovers in the background.
3. **Verified facts override documentation.** Boot logs, datasheets, and observed behaviour are ground truth. Vendor docs, product listings, and prior assumptions are hypotheses.
4. **Physical presence required.** No code may drive a GPIO or command a peripheral that is not physically wired and verified on the current board. Components in the kit but not wired in v1 must not be referenced.

## Source-of-truth files

- **`CLAUDE.md`** (this file): the rulebook. Changes rarely.
- **`PROJECT_STATE.md`**: the logbook of current state. Changes constantly. Updated through the governance process in §6.
- **`docs/modules/<modulename>.md`**: per-module documentation as modules land. Authoritative description of what each module does. CLAUDE.md remains authoritative for cross-module rules.

## Project relationship

This project is standalone. There is no shared runtime state, no shared codebase, and no shared repo with any other project. Some patterns (WiFi init shape, secrets schema, doc structure) may resemble other projects because they were ported as fresh implementations, not because they share git history.

---

# SECTION 1 — VERSIONS AND DEPENDENCIES

TBD. Populated when the project scaffolding lands.

| Item | Value |
|---|---|
| Target chip | ESP32 (original, not S3) |
| ESP-IDF version | TBD |
| esp-matter version | TBD |
| `idf_component.yml` dependencies | TBD |

---

# SECTION 2 — PIN REGISTRY

This registry is authoritative. Every GPIO used by this project is listed here. GPIOs not in this table are unassigned. Assigning a new GPIO requires updating this table in the same diff that uses it.

| Function | GPIO | Direction | Notes |
|---|---|---|---|
| Boot button | 0 | input | Strap pin, reserved. Do not assign. |
| UART0 TX | 1 | output | Default serial console. Reserved. |
| UART0 RX | 3 | input | Default serial console. Reserved. |
| SPI flash | 6–11 | — | Internal flash bus. Do not use. |
| Buzzer + | TBD | output | Active buzzer. Polarity verified before commit. |

---

# SECTION 3 — NETWORK AND SECRETS

## WiFi

- Mode: STA
- Auth: WPA2-PSK
- Power save: `WIFI_PS_NONE` (no modem sleep). Must not be removed or weakened.
- Reconnect: the WiFi module reconnects automatically on disconnect. The Matter stack resumes when WiFi returns.

## secrets.h schema

```c
#define WIFI_SSID      "..."
#define WIFI_PASSWORD  "..."
```

Additional fields are added if Matter commissioning requires them.

## Secrets rules

1. `main/secrets.h` is listed in `.gitignore` and is never committed.
2. No build artifact, log line, or diff embeds the contents of `secrets.h`. This includes partial disclosure (length, prefix, redacted-but-shaped placeholders).
3. Only files that consume a secret may `#include "secrets.h"`.

---

# SECTION 4 — BRINGUP

Placeholder sequence. Expanded as the bringup module lands.

1. WiFi init (STA, WPA2-PSK, `WIFI_PS_NONE`)
2. Matter stack init
3. GPIO buzzer init (output, default LOW)
4. Matter commissioning (first boot) or session rejoin (subsequent boots)
5. Wait for Matter On/Off commands

---

# SECTION 5 — MODULE CONTRACTS

Empty. Module contracts are added per module as modules land. Each contract covers: purpose, public API, dependencies, ownership, allowed/forbidden operations.

---

# SECTION 6 — PROJECT_STATE.md GOVERNANCE

`PROJECT_STATE.md` contains exactly four sections, in order: **Working**, **In progress**, **Blocked / deferred**, **Last verified build**. CVC may move items between these sections only when the operator has explicitly verified the move. CVC may not promote an item to "Working" on its own authority — verification is a human act.

Diffs to `PROJECT_STATE.md` go through the change handoff format like any other change. CVC reads `PROJECT_STATE.md` at the start of every session and quotes back the "In progress" and "Blocked / deferred" sections before proposing any change.

History lives in git. The file contains only the current state.

---

# SECTION 7 — CHANGE HANDOFF FORMAT

For every change, CVC produces:

1. One sentence of stated intent (plain language, what the change does).
2. A unified diff (`--- a/file`, `+++ b/file`, `@@` hunks, standard format).
3. Nothing else.

The operator relays the intent and diff to a separate validator chat. The validator returns one of four verdicts plus one sentence of reasoning:

- **APPROVE** — safe to apply
- **APPROVE WITH NOTE** — safe, but CVC should know one thing
- **REJECT** — do not apply; names the specific `CLAUDE.md` section violated
- **NEED MORE** — verdict cannot be decided from the diff alone; names exactly the one thing needed

Single-file diffs are the default. Multi-file diffs require explicit operator approval before drafting. CVC operating details are in `docs/CVC_INSTRUCTIONS.md`; validator operating details are in `docs/VALIDATOR_INSTRUCTIONS.md`.

---

# SECTION 8 — REFUSAL LIST

CVC must refuse the following without explicit operator approval in the current session. Refusing is the correct response — when refusing, name the specific item and ask whether to proceed.

1. Adding features beyond v1 scope (sensors, on-device button input, multi-tone chimes, OTA, battery monitoring, OLED display, multiple buzzers).
2. Driving any GPIO not in the pin registry, or any GPIO listed as reserved.
3. Multi-file refactors in a single diff.
4. Modifying any file under `managed_components/`.
5. Logging or otherwise disclosing values from `secrets.h`, including partial disclosure.
6. Adding cloud services beyond the Matter-to-Google-Home path.
7. Updating `PROJECT_STATE.md` outside the §6 governance process.
8. Declaring something "working" in `PROJECT_STATE.md` without explicit operator verification.
9. Removing or weakening the `WIFI_PS_NONE` setting.
10. Adding defensive timers to the runtime loop without justifying the specific physical or network failure mode the timer catches and demonstrating no other layer addresses it.

---

# END OF CLAUDE.md
