"""Coordinator tests against fake inverters on localhost.

Home Assistant is replaced by tests/ha_stubs.py; tinytuya is the real
library, talking to local sockets that behave like a misbehaving inverter.
"""

import asyncio
import logging
import socket
import threading
import time

import pytest

import ha_stubs  # noqa: F401  (must precede the integration imports)
from custom_components.sossen import coordinator as C

KEY = "0123456789abcdef"


class FakeHass:
    async def async_add_executor_job(self, func, *args):
        return await asyncio.to_thread(func, *args)


class FakeEntry:
    data = {"device_id": "x" * 22, "device_ip": "127.0.0.1", "local_key": KEY}


def _serve(handler):
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(50)
    held = []

    def run():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            handler(conn, held)

    threading.Thread(target=run, daemon=True).start()
    return srv


@pytest.fixture
def mute_inverter():
    """Accepts TCP but never answers the 3.5 handshake (wrong key / booting)."""
    srv = _serve(lambda conn, held: held.append(conn))
    yield srv.getsockname()[1]
    srv.close()


@pytest.fixture
def closed_port():
    """A port nothing listens on (inverter powered off)."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def make_coordinator(monkeypatch):
    def make(port):
        monkeypatch.setattr(C, "TUYA_PORT", port)
        monkeypatch.setattr(C, "LISTEN_WINDOW", 1)
        monkeypatch.setattr(C, "SOCKET_TIMEOUT", 0.5)
        real_device = C.tinytuya.Device
        monkeypatch.setattr(
            C.tinytuya, "Device", lambda *a, **k: real_device(*a, port=port, **k)
        )
        return C.SossenCoordinator(FakeHass(), FakeEntry())

    return make


def run_cycles(coord, n):
    async def go():
        out = None
        for _ in range(n):
            out = await coord._async_update_data()
        return out

    return asyncio.run(go())


def count_probes(coord):
    calls = {"n": 0}
    real = coord._sync_probe

    def probe():
        calls["n"] += 1
        return real()

    coord._sync_probe = probe
    return calls


def test_powered_off_inverter_cycles_fast_then_reports_off(
    make_coordinator, closed_port, monkeypatch
):
    monkeypatch.setattr(C, "MAX_FAILED_POLLS", 3)
    coord = make_coordinator(closed_port)
    coord.data = {"ac_power_w": 90, "ac_voltage_v": 230.0, "energy_total_kwh": 1280.0}

    start = time.monotonic()
    data = run_cycles(coord, 3)
    elapsed = time.monotonic() - start

    # Before v1.5.0 one cycle against a dead port took 90-105 s.
    assert elapsed < 20
    assert coord.is_powered_off
    assert data["status"] == 0
    assert data["ac_power_w"] == 0            # production really is 0
    assert data["ac_voltage_v"] is None       # nothing to measure
    assert data["energy_total_kwh"] == 1280.0  # odometer never drops


def test_refused_handshake_warns_once(make_coordinator, mute_inverter, monkeypatch, caplog):
    monkeypatch.setattr(C, "HANDSHAKE_REFUSALS_WARN_AFTER", 3)
    coord = make_coordinator(mute_inverter)

    with caplog.at_level(logging.WARNING, logger=C.__name__):
        run_cycles(coord, 5)

    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "914" in warnings[0] and "Local Key" in warnings[0]


def test_lost_session_is_probed_but_silence_is_not(make_coordinator, mute_inverter):
    coord = make_coordinator(mute_inverter)
    probes = count_probes(coord)

    run_cycles(coord, 2)  # first cycle loses the session, second probes first
    assert coord._session_lost
    assert probes["n"] >= 1

    # A live session that merely hears nothing (stream re-arming) must not
    # open a second socket next to it.
    coord._session_lost = False
    probes["n"] = 0

    def silent_listen():
        coord._session_lost = False
        coord._session_err = None
        return None

    coord._sync_listen = silent_listen
    run_cycles(coord, 3)
    assert probes["n"] == 0


def test_data_resets_failure_counters(make_coordinator, mute_inverter):
    coord = make_coordinator(mute_inverter)
    run_cycles(coord, 2)
    assert coord._failed_polls == 2
    assert coord._handshake_refusals == 2

    frame = {"status": 3, "ac_power_w": 100}
    coord._sync_listen = lambda: frame
    coord._limit_read_pending = False

    assert run_cycles(coord, 1) is frame
    assert coord._failed_polls == 0
    assert coord._handshake_refusals == 0


class FakeEntries:
    def async_update_entry(self, entry, data=None, options=None):
        if data is not None:
            entry.data = data


def coordinator_with_entries(make_coordinator, port):
    coord = make_coordinator(port)
    coord.entry = type("E", (), {"data": dict(FakeEntry.data), "options": {}})()
    coord.hass.config_entries = FakeEntries()
    return coord


def test_limit_command_rearms_stream_counts_and_tracks_user_limit(
    make_coordinator, closed_port
):
    coord = coordinator_with_entries(make_coordinator, closed_port)
    coord._sync_set_power_limit = lambda watts: True
    coord._last_push = 12345.0

    assert asyncio.run(coord.async_set_power_limit(600))
    # The command reply interrupts the push stream: re-arm next cycle.
    assert coord._last_push == 0.0
    assert coord.power_limit == 600 and coord.user_limit == 600
    assert coord.limit_commands == 1

    # The protection lowering it keeps the user's choice as the ceiling.
    assert asyncio.run(coord.async_set_power_limit(530, by_user=False))
    assert coord.power_limit == 530 and coord.user_limit == 600
    assert coord.limit_commands == 2


def test_failed_limit_command_changes_nothing(make_coordinator, closed_port):
    coord = coordinator_with_entries(make_coordinator, closed_port)
    coord._sync_set_power_limit = lambda watts: False

    assert not asyncio.run(coord.async_set_power_limit(600))
    assert coord.limit_commands == 0


def test_unreachable_inverter_is_looked_for_at_a_new_address(
    make_coordinator, closed_port, mute_inverter, monkeypatch
):
    """DHCP moved it: the off-probe finds it by broadcast and follows it."""
    coord = coordinator_with_entries(make_coordinator, closed_port)
    coord.is_powered_off = True
    probes = {"n": 0}

    def probe():
        probes["n"] += 1
        return coord._device_ip == "10.9.9.9"

    coord._sync_probe = probe

    async def locate(hass, keys, timeout, sweep=False):
        return {next(iter(keys)): "10.9.9.9"}

    monkeypatch.setattr(C, "async_locate", locate)
    monkeypatch.setattr(C, "REDISCOVER_EVERY_OFF_PROBES", 2)
    coord._sync_listen = lambda: None

    run_cycles(coord, 1)
    assert coord.is_powered_off  # first off-probe: no search yet
    run_cycles(coord, 1)
    assert not coord.is_powered_off
    assert coord._device_ip == "10.9.9.9"
    assert coord.entry.data["device_ip"] == "10.9.9.9"
