# PROJECT_STATE.md

## Working

- **Doorbell device (`doorbell-buzzer`).** ESP32 (board id `esp32dev`)
  running ESPHome firmware 2026.4.5, adopted into the local HA instance
  as `doorbell-buzzer`. Provides the doorbell chime triggered by Nest
  doorbell events, plus ambient environmental data at the doorbell
  location.
  - **Hardware:** passive piezo on GPIO 23 driven by ESPHome `rtttl`;
    DHT11 temp/humidity sensor on GPIO 32. Pin assignments authoritative
    in `CLAUDE.md` §2.
  - **Entities exposed to HA:** `button.doorbell_buzzer_play_chime`
    (plays one ding-dong RTTTL melody on press),
    `sensor.doorbell_buzzer_temperature` and
    `sensor.doorbell_buzzer_humidity` (integer-precision readings at
    60s update interval).
  - **Cloud event ingress:** HA Nest integration adopted via Google
    Device Access + Cloud Pub/Sub, per §8 refusal #7's carve-out.
    Surfaces `doorbell_chime` events from the Google Nest Doorbell
    GWX3T to HA's event bus. End-to-end latency ~8-10s, bounded by
    Google Pub/Sub — see §5 module contract for why this is not a
    project-controlled latency.
  - **Automation:** an HA-side automation (operator-authored in HA's
    Automations UI, not in this repo per refusal #5) listens for
    `doorbell_chime` from the Nest device and presses
    `button.doorbell_buzzer_play_chime` five times with 1-second
    waits, producing ~5 seconds of chime per doorbell press. Contract
    shape in `CLAUDE.md` §5 "Audio output (doorbell chime)".
  - **Networking and security:** WiFi `power_save_mode: NONE` per
    refusal #10; encrypted native API to HA per refusal #11.
  - **Deploy path:** this repo's `esphome/` is cloned to
    `/config/esp_home_hub/` on the HA host and symlinked to
    `/config/esphome/`; the ESPHome dashboard reads from the symlink.
  - **Verification status:** all four sub-systems (buzzer toggle,
    Nest ingress, chime via automation, environmental sensors) have
    been operator-verified at the physical doorbell. Per-promotion
    verification history lives in git and in the "Last verified
    deploy" section below.

## In progress

(Empty.)

## Blocked / deferred

- Battery power — deferred to v2.
- Multi-tone chime — deferred to v2.
- Physical doorbell button reader on the device — deferred to v2 (the trigger comes from HA automations, not from a button on this device).
- OLED status display (kit-included) — deferred to v2; kept in mind for later.
- PIR motion sensor (kit-included) — deferred to v2; the trigger is an HA-side automation, not local motion.
- Other kit sensors (photoresistor, obstacle avoidance) — out of scope for v1. (DHT11 was previously listed here; it has been admitted via refusal #1 carve-out and is tracked under In progress.)

## Last verified deploy

- 2026-05-16: `doorbell-buzzer.yaml` compiled and flashed via USB; device
  adopted into HA, `switch.buzzer` toggles confirmed.
- 2026-05-16: Nest event ingress + buzzer automation wired end-to-end.
  Doorbell button press → ~10s ingress latency → 5-second buzz, then
  silent. Verified by operator at the physical doorbell.
- 2026-05-16: Audio output swapped to passive piezo, RTTTL chime at
  octave 7. End-to-end verified at the physical doorbell — five
  ding-dongs at acceptable volume in the operator's mounting room.
- 2026-05-17: DHT11 sensor reporting. After bring-up debugging
  (DATA pin moved across several GPIOs before the wiring issue was
  found), settled on GPIO 32. Temperature and humidity entities
  confirmed visible in HA with plausible integer readings.
- Versions verified: HA OS 17.3, ESPHome add-on 2026.4.5, ESPHome
  framework 2026.4.5.
