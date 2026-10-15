"""SOSSEN proprietary protocol encoder/decoder."""

import base64

from .dp import (
    DP_AC_FREQUENCY,
    DP_AC_POWER,
    DP_AC_VOLTAGE,
    DP_DC_CURRENT_1,
    DP_DC_CURRENT_2,
    DP_DC_CURRENT_3,
    DP_DC_CURRENT_4,
    DP_DC_POWER_1,
    DP_DC_POWER_2,
    DP_DC_POWER_3,
    DP_DC_POWER_4,
    DP_DC_POWER_TOTAL,
    DP_DC_VOLTAGE_1,
    DP_DC_VOLTAGE_2,
    DP_DC_VOLTAGE_3,
    DP_DC_VOLTAGE_4,
    DP_ENERGY_AC_UNITS,
    DP_ENERGY_DC_UNITS,
    DP_ENERGY_TOTAL,
    DP_SET_FLAG,
    DP_SET_POWER_LIMIT,
    DP_STATUS,
    DP_TEMPERATURE,
)


def decode_records(payload_b64: str) -> dict:
    """Decode a Base64 payload into raw DP records (id -> value)."""
    try:
        data = base64.b64decode(payload_b64)
    except Exception:
        return {}

    records = {}
    i = 2
    while i + 6 <= len(data):
        if data[i] == 0x01 and data[i + 1] == 0x01:
            dp_id = (data[i + 2] << 8) | data[i + 3]
            value = (data[i + 4] << 8) | data[i + 5]
            records[dp_id] = value
            i += 6
        else:
            # Real payloads are not a clean run of 6-byte records (there are
            # gap/padding bytes between DP blocks, e.g. around DP 4152/4156),
            # so resync one byte at a time to the next 0x01 0x01 marker rather
            # than stopping. Breaking here aborts before DP_AC_VOLTAGE (4103)
            # is read, which makes decode_payload() return None and every
            # sensor read as off — the whole payload is discarded. The stray
            # 0x01 0x01-inside-a-value risk is tolerated: decode_payload gates
            # on DP_AC_VOLTAGE and the DP ids are sane, so it never mattered
            # in practice.
            i += 1
    return records


def _signed(value: int) -> int:
    """Interpret a 16-bit raw value as two's complement."""
    return value - 0x10000 if value >= 0x8000 else value


def decode_payload(payload_b64: str) -> dict | None:
    """Decode a SOSSEN proprietary Base64 payload into sensor values.

    Payload format:
    - Header: 2 bytes (0x03 0x01)
    - Records: 6 bytes each (0x01 0x01 DPH DPL VH VL)
    """
    records = decode_records(payload_b64)

    if not records or DP_AC_VOLTAGE not in records:
        return None

    # Absent energy record -> None, never 0: a TOTAL_INCREASING sensor
    # dropping to 0 is read as a meter reset by the Energy dashboard.
    energy_raw = records.get(DP_ENERGY_TOTAL)

    result = {
        "status": records.get(DP_STATUS, 0),
        "dc_power_total_w": round(records.get(DP_DC_POWER_TOTAL, 0) * 1.0, 1),
        "ac_power_w": round(records.get(DP_AC_POWER, 0) * 1.0, 1),
        "ac_voltage_v": round(records.get(DP_AC_VOLTAGE, 0) * 0.1, 1),
        "ac_frequency_hz": round(records.get(DP_AC_FREQUENCY, 0) * 0.01, 2),
        "dc_voltage_1_v": round(records.get(DP_DC_VOLTAGE_1, 0) * 0.1, 1),
        "dc_current_1_a": round(records.get(DP_DC_CURRENT_1, 0) * 0.03125, 2),
        "dc_power_1_w": round(records.get(DP_DC_POWER_1, 0) * 0.33, 1),
        "dc_voltage_2_v": round(records.get(DP_DC_VOLTAGE_2, 0) * 0.1, 1),
        "dc_current_2_a": round(records.get(DP_DC_CURRENT_2, 0) * 0.03125, 2),
        "dc_power_2_w": round(records.get(DP_DC_POWER_2, 0) * 0.33, 1),
        "dc_voltage_3_v": round(records.get(DP_DC_VOLTAGE_3, 0) * 0.1, 1),
        "dc_current_3_a": round(records.get(DP_DC_CURRENT_3, 0) * 0.03125, 2),
        "dc_power_3_w": round(records.get(DP_DC_POWER_3, 0) * 0.33, 1),
        "dc_voltage_4_v": round(records.get(DP_DC_VOLTAGE_4, 0) * 0.1, 1),
        "dc_current_4_a": round(records.get(DP_DC_CURRENT_4, 0) * 0.03125, 2),
        "dc_power_4_w": round(records.get(DP_DC_POWER_4, 0) * 0.33, 1),
        "energy_total_kwh": (
            round(energy_raw * 0.1, 1) if energy_raw is not None else None
        ),
        "temperature_c": round(_signed(records.get(DP_TEMPERATURE, 0)) * 0.1, 1),
    }

    # Lifetime conversion efficiency and DC-side energy, derived from the two
    # monotonic energy counters (4172 = AC, 4174 = DC). The efficiency is a
    # pure ratio (unit-independent); DC energy is anchored to the accurate AC
    # odometer (DP 4098) scaled by the counter ratio, so no magic kWh-per-unit
    # constant is baked in. Both are None when the counters are absent (older
    # firmware / other models) so nothing shows a bogus 0. They are also None
    # when AC exceeds DC: output can never exceed input, so the registers do
    # not hold the energy counters (an early-2026 firmware reported 92/90
    # there, which would read as 102% efficiency).
    ac_units = records.get(DP_ENERGY_AC_UNITS, 0)
    dc_units = records.get(DP_ENERGY_DC_UNITS, 0)
    if ac_units and dc_units and ac_units <= dc_units:
        result["conversion_efficiency_lifetime"] = round(
            ac_units / dc_units * 100, 1
        )
        ac_energy = result["energy_total_kwh"]
        result["energy_dc_total_kwh"] = (
            round(ac_energy * dc_units / ac_units, 1)
            if ac_energy is not None else None
        )
    else:
        result["conversion_efficiency_lifetime"] = None
        result["energy_dc_total_kwh"] = None

    # Include all raw DP values for debugging
    result["_raw"] = {str(k): v for k, v in sorted(records.items())}

    return result


def build_set_power_payload(watts: int) -> str:
    """Build a Base64 payload to set the inverter power limit.

    Uses Tuya DP 24 with proprietary records:
    - DP 32770 (0x8002) = 0 (flag)
    - DP 32771 (0x8003) = watts
    """
    if not 0 <= watts <= 0xFFFF:
        raise ValueError(f"Power limit out of range: {watts}")
    vh = (watts >> 8) & 0xFF
    vl = watts & 0xFF
    payload = bytes(
        [
            0x03, 0x01,
            0x01, 0x01, (DP_SET_FLAG >> 8) & 0xFF, DP_SET_FLAG & 0xFF, 0x00, 0x00,
            0x01, 0x01, (DP_SET_POWER_LIMIT >> 8) & 0xFF, DP_SET_POWER_LIMIT & 0xFF, vh, vl,
        ]
    )
    return base64.b64encode(payload).decode()
