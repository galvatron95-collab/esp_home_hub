# PROJECT_STATE.md

## Working

(Empty — project not yet started.)

## In progress

- Project scaffolding: ESP-IDF project structure, esp-matter dependency, hello-world Matter device commissioning into Google Home. Toolchain side unblocked on WSL Ubuntu 22.04 with Python 3.11 — `install.sh --no-host-tool` runs clean against the §1-pinned esp-matter (main @ `4d21fe5`); `idf.py build` not yet re-attempted on this combination.

## Blocked / deferred

- OTA updates — deferred to v2.
- Battery power — deferred to v2.
- Multi-tone chime — deferred to v2.
- Physical doorbell button reader on the device — deferred to v2 (the trigger comes from Google Home, not from a button on this device).
- OLED status display (kit-included) — deferred to v2; kept in mind for later.
- PIR motion sensor (kit-included) — deferred to v2; the trigger is Google Home's PersonDetection, not local motion.
- Other kit sensors (DHT11 temp/humidity, photoresistor, obstacle avoidance) — out of scope for v1.

## Last verified build

(None yet — project not yet built.)
