# SOSSEN Microinverter - Home Assistant Integration

Local integration for **SOSSEN 2in1** (600W / 800W / 1000W) and **SOSSEN 4in1** (2400W) microinverters for Home Assistant.

Direct communication over the **local network** via Tuya v3.5 protocol — no cloud, no delays, no external servers.

## What it does

This integration reads production data directly from the SOSSEN microinverter and exposes them as sensors in Home Assistant, refreshed every ~20 seconds from the inverter's own real-time data stream.

### Available sensors

| Sensor | Description | Unit |
|--------|-------------|------|
| DC Total Power | Combined power from all panels | W |
| AC Power | Inverter output power (fed to grid) | W |
| AC Voltage | Grid voltage | V |
| AC Frequency | Grid frequency | Hz |
| DC Voltage 1-4 | Input voltage from each panel | V |
| DC Current 1-4 | Input current from each panel | A |
| DC Power 1-4 | Power from each panel | W |
| Total Energy | Cumulative energy counter (odometer style) | kWh |
| Temperature | Inverter internal temperature | C |
| Inverter Status | Operating state: producing / alarm / off | — |

Channel 3/4 sensors are created only for 4in1 models.

### Controls

| Control | Description |
|---------|-------------|
| Power Limit | Set the output power limit (0 W up to the model maximum) |

> **About the power limit**: the inverter does not jump to a new limit, it ramps towards it. Going down takes about 1–2 minutes to get close (a few minutes more for the last watts near 0 W), going back up about 1–2 minutes. The output may settle somewhat below the limit. Keep this in mind when driving the limit from an automation (e.g. zero-export): change it at most every couple of minutes, or the automation will chase its own ramp.

### Diagnostics

| Sensor | Description |
|--------|-------------|
| Raw Data | Shows all raw register (DP) values as attributes (disabled by default — enable it from *Settings → Devices & Services → Entities*) |
| Conversion Efficiency (lifetime) | Lifetime AC/DC energy ratio (%); a slow drop is an early sign of a degrading microinverter |
| DC Energy (lifetime) | Lifetime energy taken from the panels, input side (kWh) |

## Supported models

The model is selected during setup:

- **SOSSEN 2in1** — 2 panels, 600W / 800W / 1000W (800W tested by the author; 1000W is a software upgrade of the same hardware)
- **SOSSEN 4in1** — 4 panels, 2400W (channel 3/4 register mapping contributed and verified by the community — thanks [@flrs-94](https://github.com/flrs-94) and [@Loudramin](https://github.com/Loudramin))

Potentially compatible with other SOSSEN microinverters using the Tuya v3.5 protocol with proprietary Base64 payload. If you have a different model and it works (or doesn't), please open an issue!

## On/off detection

The inverter has no standby power: without sun (or a battery) it switches off completely. The integration deduces its state from the network — no sun-position guessing, so **battery-equipped systems producing at night are fully supported**:

- While the inverter answers polls, it is **on** and data flows normally
- If it stops answering but its TCP port is still reachable, it is powered but something is wrong: all entities become **unavailable** (stale data is never shown as fresh)
- If its TCP port becomes unreachable, it is **powered off**: polling slows down to a light TCP probe every 60 seconds (resuming automatically at the normal rate as soon as power is back), and an honest "off" state is reported instead of freezing the last values:
  - **Power and current sensors drop to 0** — production really is zero
  - **Voltage, frequency and temperature sensors become unavailable** — there is nothing to measure
  - **Total Energy keeps its last value** — it is an odometer-style counter, so the Energy dashboard is never corrupted by a fake meter reset
  - **Inverter Status shows "Off"**

## Languages

English, Italian, German and French (config flow and entity names).

## Requirements

- Home Assistant 2024.4.0 or higher
- The inverter must be on the same local network as Home Assistant
- Required: **Device ID**, **local IP**, and **Local Key**

## Installation

### Via HACS (recommended)

1. Open HACS in Home Assistant
2. Go to **Integrations** → three-dot menu → **Custom repositories**
3. Add `https://github.com/caveman2024/sossen-ha` as type **Integration**
4. Search for "SOSSEN" and install
5. Restart Home Assistant
6. Go to **Settings → Devices & Services → Add Integration** → search for "SOSSEN"

### Manual

1. Copy the `custom_components/sossen/` folder into your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant
3. Go to **Settings → Devices & Services → Add Integration** → search for "SOSSEN"

## Important

> **⚠️ The inverter has no standby power** — it is completely off when it has no power source (no sun and no battery). Configuration and first data reception can only happen while the inverter is powered. After connecting, allow **2-3 minutes** before data starts appearing.

## Configuration

During setup you will be asked for:

| Field | Where to find it |
|-------|-----------------|
| **Device IP** | Check the DHCP client list in your router |
| **Device ID** | Smart Life app → your inverter → Settings (gear icon top right) → Device Information → Virtual ID |
| **Local Key** | [Tuya IoT Platform](https://iot.tuya.com) → create a project → link your Smart Life account → Devices → All Devices → Local Key |

### How to obtain the Local Key

1. Go to [iot.tuya.com](https://iot.tuya.com) and create an account
2. Create a new Cloud project (Data Center: Europe, or your region)
3. Under **Devices** → **Link Tuya App Account** → scan the QR code from the Smart Life app
4. Go to **Devices → All Devices** — you will find your inverter with its **Local Key**

**Alternative**: [tuya-local-key](https://github.com/vineetchoudhary/tuya-local-key) extracts the Device IDs and Local Keys of all your devices in one go (thanks @velectris491 for the tip).

> **Note**: Tuya's own support assistant may claim that the Local Key can no longer be retrieved. It still can — see also [this step-by-step guide](https://www.remcokersten.nl/posts/get-tuya-localkey/) (thanks @TheB1gG).

> **⚠️ The Local Key changes every time the inverter is removed and re-added in the app.** If you re-paired it, fetch the key again and re-add the integration. A wrong key shows up in the log as *"Inverter session refused (Tuya error 914)"*.
>
> **Using the Home Power app?** Remove the inverter from Home Power first, then add it in Smart Life (a device can only be bound to one account).

> **Tip**: It is recommended to configure a static IP for the inverter in your router, so it doesn't change on reboot.

## How it works

The integration uses [TinyTuya](https://github.com/jasonacox/tinytuya) to communicate directly with the inverter over the local network. The integration keeps a Tuya v3.5 session open and puts the inverter into its real-time broadcast mode (the same thing the vendor app does when opened): the inverter then pushes a proprietary Base64 payload with all production data every ~5 seconds, and the integration re-arms the stream automatically whenever it goes quiet.

The payload is decoded according to the SOSSEN format: 2-byte header + 6-byte records, where each record contains the register ID (DP) and its associated value.

### Important technical note

Keeping the Smart Life app open at the same time is fine. Other local software talking to the same inverter (Python scripts, tuya-local, another copy of this integration…) can interfere with the session, so disable it before using this integration.

## Recommended dashboard

For an animated energy flow dashboard, install [Power Flow Card Plus](https://github.com/flixlix/power-flow-card-plus) from HACS.

## Author

Developed by **caveman2024**.

Born from the need to monitor a residential solar system with Astronergy N7S 450W bifacial panels, after discovering that standard Tuya integrations do not support the proprietary SOSSEN protocol.

## License

MIT

## Contributing

Bug reports and pull requests are welcome on [GitHub](https://github.com/caveman2024/sossen-ha/issues).

If you have a SOSSEN inverter with a different model, your raw data (from the "Raw Data" diagnostic sensor) is valuable for mapping new registers!
