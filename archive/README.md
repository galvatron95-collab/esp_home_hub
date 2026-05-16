# Archive — esp-matter / Google Home era

This directory holds the original v1 design of this project: an ESP32 running
esp-matter, commissioned into Google Home as an On/Off Light, with the buzzer
driven by Matter On/Off commands.

That design has been retired. Nothing under `archive/` is built, deployed, or
referenced by the live project. It is kept in-tree (rather than deleted) so the
pivot is legible from the repo alone, without digging through git history.

## Why the pivot

1. **Controller availability.** Google Home's Matter controller is not generally
   available in Australia. Commissioning a Matter device into Google Home from
   AU is not a supported path as of the pivot date.
2. **Data sovereignty.** Routing doorbell events through Google's cloud
   conflicts with the project's preference for local-first control. Even with a
   working Matter path, the data flow was wrong.

## What replaced it

A self-hosted Home Assistant OS instance (Lenovo ThinkCentre Tiny, on the LAN)
running the ESPHome add-on. The ESP32 runs ESPHome firmware instead of
esp-matter, exposes the buzzer as a `switch` entity, and is controlled by HA
automations. Deployment is git-pull-then-Install via the HA UI.

See `CLAUDE.md` and `PROJECT_STATE.md` at the repo root for the live contract
and current state.
