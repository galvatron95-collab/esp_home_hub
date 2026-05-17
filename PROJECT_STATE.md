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
- Audio output swap: passive piezo on GPIO 23 driven by ESPHome `rtttl`,
  replacing the original active buzzer per §8 refusal #1's narrow
  carve-out. Exposed to HA as `button.doorbell_buzzer_play_chime` (no
  longer `switch.buzzer`). DoorBuzzer automation rebuilt to press the
  button 5 times with 1-second waits. Verified by pressing the physical
  doorbell and hearing five ding-dongs at acceptable volume.
- Environmental sensor (DHT11) on the doorbell-buzzer device, GPIO 32.
  Two new HA entities: `sensor.doorbell_buzzer_temperature` and
  `sensor.doorbell_buzzer_humidity`, reporting ambient values at
  integer precision per the §5 module contract. Verified by the
  operator opening HA and confirming the two entities show real
  numbers rather than `unavailable`.

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
