"""Protocol codec tests — no Home Assistant needed."""

import base64

import pytest

from custom_components.sossen import dp
from custom_components.sossen.protocol import (
    build_set_power_payload,
    decode_payload,
    decode_records,
)

# Real DP 21 frame captured from a 2in1 800 W on 2026-03-30 (debug log).
REAL_FRAME_HEX = (
    "03010101100000030101100100000101100200b70101102600000101102700350101104800"
    "000101104900380101100709b60101101a00160101101d00000101101e00350101102313890101"
    "1028138901011030000001011031001c01011032014601011033005601011034000001011035"
    "001b0101103601470101103700550101104b00000101104c005c0101104d00000101104e005a"
    "010110570114010110590114"
)


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def frame(records: dict[int, int], header: bytes = b"\x03\x01") -> str:
    """Encode {dp: value} as a SOSSEN payload."""
    body = b"".join(
        bytes([0x01, 0x01, dp_id >> 8, dp_id & 0xFF, value >> 8, value & 0xFF])
        for dp_id, value in records.items()
    )
    return b64(header + body)


def test_real_frame_decodes_to_physical_values():
    data = decode_payload(b64(bytes.fromhex(REAL_FRAME_HEX)))

    assert data["status"] == 3
    assert data["ac_voltage_v"] == 248.6
    assert data["ac_power_w"] == 53
    assert data["ac_frequency_hz"] == 50.01
    assert data["dc_power_total_w"] == 56
    assert data["dc_voltage_1_v"] == 32.6
    assert data["dc_current_1_a"] == 0.88  # 28 * 0.03125 = 0.875
    assert data["dc_power_1_w"] == pytest.approx(28.4, abs=0.05)
    assert data["dc_voltage_2_v"] == 32.7
    assert data["dc_current_2_a"] == 0.84  # 27 * 0.03125 = 0.84375
    assert data["energy_total_kwh"] == 18.3
    assert data["temperature_c"] == 27.6
    # Every record is kept for the Raw Data diagnostic sensor.
    assert data["_raw"]["4103"] == 2486


def test_decoder_resyncs_over_padding_between_records():
    """Gap bytes between DP blocks must not abort decoding (v1.1 bug)."""
    good = base64.b64decode(frame({dp.DP_STATUS: 3, dp.DP_AC_VOLTAGE: 2300}))
    padded = good[:8] + b"\x00\x07\x42" + good[8:]

    records = decode_records(b64(padded))

    assert records[dp.DP_STATUS] == 3
    assert records[dp.DP_AC_VOLTAGE] == 2300


def test_frame_without_ac_voltage_is_not_a_data_frame():
    """DP 25 / DP 23 frames carry no AC voltage and must be ignored."""
    assert decode_payload(frame({dp.DP_STATUS: 3, 4097: 0})) is None


def test_invalid_base64_is_ignored():
    assert decode_records("not base64 !!!") == {}
    assert decode_payload("not base64 !!!") is None


def test_missing_energy_is_none_not_zero():
    """A TOTAL_INCREASING sensor dropping to 0 reads as a meter reset."""
    data = decode_payload(frame({dp.DP_AC_VOLTAGE: 2300}))
    assert data["energy_total_kwh"] is None


def test_temperature_is_signed():
    data = decode_payload(frame({dp.DP_AC_VOLTAGE: 2300, dp.DP_TEMPERATURE: 0x10000 - 52}))
    assert data["temperature_c"] == -5.2


def test_lifetime_efficiency_and_dc_energy():
    data = decode_payload(
        frame(
            {
                dp.DP_AC_VOLTAGE: 2300,
                dp.DP_ENERGY_TOTAL: 10040,  # 1004.0 kWh
                dp.DP_ENERGY_AC_UNITS: 4900,
                dp.DP_ENERGY_DC_UNITS: 5140,
            }
        )
    )
    assert data["conversion_efficiency_lifetime"] == 95.3
    assert data["energy_dc_total_kwh"] == pytest.approx(1004.0 * 5140 / 4900, abs=0.05)


def test_lifetime_values_absent_without_counters():
    data = decode_payload(frame({dp.DP_AC_VOLTAGE: 2300, dp.DP_ENERGY_TOTAL: 100}))
    assert data["conversion_efficiency_lifetime"] is None
    assert data["energy_dc_total_kwh"] is None


@pytest.mark.parametrize("watts", [0, 50, 800, 1000, 2400])
def test_set_power_payload_round_trip(watts):
    raw = base64.b64decode(build_set_power_payload(watts))

    assert raw[:2] == b"\x03\x01"
    assert decode_records(build_set_power_payload(watts)) == {
        dp.DP_SET_FLAG: 0,
        dp.DP_SET_POWER_LIMIT: watts,
    }


@pytest.mark.parametrize("watts", [-1, 0x10000])
def test_set_power_payload_rejects_out_of_range(watts):
    with pytest.raises(ValueError):
        build_set_power_payload(watts)
