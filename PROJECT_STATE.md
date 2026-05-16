# PROJECT_STATE.md

## Working

- Doorbell buzzer ESPHome device. ESP32 (board id `esp32dev`) running
  ESPHome firmware 2026.4.5, active buzzer on GPIO 23, exposed to the
  local HA instance as `switch.buzzer`. Survives HA-side switch toggles
  with sub-second latency per the §0 quality bar. WiFi `power_save_mode:
  NONE` per refusal #10, encrypted native API per refusal #11.
  Deployment path: this repo's `esphome/` is cloned to
  `/config/esp_home_hub/` on the HA host and symlinked to
  `/config/esphome/`.
- Google Nest doorbell → buzzer end-to-end. HA Nest integration adopted;
  HA automation (operator-authored in HA UI per §5 module contract)
  fires on `doorbell_chime` from the Nest doorbell device, drives
  `switch.buzzer` ON for 5 seconds, OFF. Verified by pressing the
  physical doorbell button and hearing the buzzer beep for the expected
  duration.

## In progress

- Swap doorbell audio output from active buzzer to passive piezo on
  GPIO 23, driven by ESPHome `rtttl`. Driven by §0's loudness
  observation, scope-admitted by §8 refusal #1's narrow carve-out for
  a single audio output. Steps:
    1. Operator physically swaps the active buzzer for the passive
       piezo on GPIO 23 (refusal #5: hardware work, not CVC's).
    2. CVC drafts the `esphome/doorbell-buzzer.yaml` swap (active-high
       `switch` platform → `rtttl` output + a switch or button entity
       HA's existing automation can trigger). Single chime melody only,
       per refusal #1.
    3. Operator flashes, presses the doorbell, confirms the new chime
       sounds and is at an acceptable volume. CVC then promotes via a
       PROJECT_STATE.md / §5 module-contract diff.

## Blocked / deferred

- Battery power — deferred to v2.
- Multi-tone chime — deferred to v2.
- Physical doorbell button reader on the device — deferred to v2 (the trigger comes from HA automations, not from a button on this device).
- OLED status display (kit-included) — deferred to v2; kept in mind for later.
- PIR motion sensor (kit-included) — deferred to v2; the trigger is an HA-side automation, not local motion.
- Other kit sensors (DHT11 temp/humidity, photoresistor, obstacle avoidance) — out of scope for v1.

## Last verified deploy

- 2026-05-16: `doorbell-buzzer.yaml` compiled and flashed via USB; device
  adopted into HA, `switch.buzzer` toggles confirmed.
- 2026-05-16: Nest event ingress + buzzer automation wired end-to-end.
  Doorbell button press → ~10s ingress latency → 5-second buzz, then
  silent. Verified by operator at the physical doorbell.
- Versions verified: HA OS 17.3, ESPHome add-on 2026.4.5, ESPHome
  framework 2026.4.5.
