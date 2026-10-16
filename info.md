# SOSSEN Microinverter

Local integration for **SOSSEN 2in1** (600W / 800W / 1000W) and **SOSSEN 4in1** (2400W) microinverters.

Direct communication over the local network — no cloud, no delays.

## Features

* **Smart Life login**: set up with a QR code, no Local Key or IP to look up (the account is used only during setup)
* **Automatic LAN discovery**, also when the inverter's IP changes
* **Overvoltage protection** (optional): lowers the power limit before the grid voltage trips the inverter
* Real-time solar production sensors (power, voltage, current, frequency, temperature, energy) — per-panel channels included
* **Inverter status** with 3 states: producing, alarm, off
* **Power limit control** directly from HA (0 W up to the model maximum)
* **Automatic on/off detection** from the network (battery systems producing at night are supported): when off, power drops to 0, unmeasurable values go unavailable, the energy counter is preserved
* **Diagnostic sensor** with all raw register values
* **Real-time** updates (~20 seconds) from the inverter's own data stream
* Compatible with HA **Energy Dashboard**
* Available in **English, Italian, German and French**

## Requirements

* The inverter must be on the same local network as Home Assistant
* You will need: Device ID, local IP, and Local Key (obtainable from the [Tuya IoT Platform](https://iot.tuya.com))

## Supported models

* SOSSEN 2in1 — 600W / 800W / 1000W (2 panels)
* SOSSEN 4in1 — 2400W (4 panels, community-verified register mapping)
