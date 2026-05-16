# PROJECT_STATE.md

## Working

(Empty — project not yet started.)

## In progress

- First ESPHome buzzer device, adopted into the local HA instance. Prerequisite steps, in order:
    1. Confirm HA OS version and ESPHome add-on version (record in §1 of `CLAUDE.md` and here under "Last verified deploy" once a deploy lands).
    2. Install the SSH or Studio Code Server add-on on the HA host (operator-side, one-time).
    3. Clone this repo's `esphome/` subdirectory to `/config/esphome/` on the HA host (operator-side, one-time).
    4. Write the first device YAML at `esphome/doorbell-buzzer.yaml` (CVC, via §7 diff).
    5. Create `esphome/secrets.yaml` on the operator workstation from the schema in `CLAUDE.md` §3 (operator; never committed) and ensure the HA-side clone has its own copy.
    6. Operator pushes, pulls on HA, clicks Install in the ESPHome dashboard, flashes the ESP32 over USB for first adoption.
    7. Confirm the device adopts into HA and the buzzer responds to switch toggles within the §0 quality bar.
  All seven steps unverified at time of writing — the project just pivoted off esp-matter and onto this architecture; no ESPHome firmware has been built or flashed yet.

## Blocked / deferred

- Battery power — deferred to v2.
- Multi-tone chime — deferred to v2.
- Physical doorbell button reader on the device — deferred to v2 (the trigger comes from HA automations, not from a button on this device).
- OLED status display (kit-included) — deferred to v2; kept in mind for later.
- PIR motion sensor (kit-included) — deferred to v2; the trigger is an HA-side automation, not local motion.
- Other kit sensors (DHT11 temp/humidity, photoresistor, obstacle avoidance) — out of scope for v1.

## Last verified deploy

(None yet — no ESPHome firmware has been deployed.)
