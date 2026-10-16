"""Overvoltage protection: the decision, and the controller around it."""

import asyncio
from types import SimpleNamespace

import ha_stubs  # noqa: F401  (must precede the integration imports)
from custom_components.sossen.const import PROTECTION_DEFAULTS
from custom_components.sossen.protection import (
    OvervoltageProtection,
    next_limit,
    protection_errors,
    protection_settings,
)

S = protection_settings({})  # defaults: 249 / 245 V, -70 / +30 W, floor 300


def test_defaults_are_off():
    assert S == PROTECTION_DEFAULTS
    assert S["protection"] is False


def test_high_voltage_lowers_one_step():
    assert next_limit(S, 250.1, 800, current=800, ceiling=800) == 730


def test_never_below_floor():
    assert next_limit(S, 252.0, 320, current=320, ceiling=800) == 300
    assert next_limit(S, 252.0, 300, current=300, ceiling=800) == 300


def test_floor_never_above_the_users_limit():
    # User chose 200 W, below the 300 W floor: the floor gives way.
    assert next_limit(S, 252.0, 200, current=200, ceiling=200) == 200


def test_low_voltage_raises_slowly_up_to_the_users_limit():
    assert next_limit(S, 240.0, 725, current=730, ceiling=800) == 760
    assert next_limit(S, 240.0, 790, current=790, ceiling=800) == 800
    assert next_limit(S, 240.0, 800, current=800, ceiling=800) == 800


def test_no_raise_when_not_held_by_the_limit():
    """Clouds or evening: producing well below the limit, raising is useless."""
    assert next_limit(S, 240.0, 300, current=730, ceiling=800) == 730


def test_dead_band_changes_nothing():
    assert next_limit(S, 247.0, 700, current=730, ceiling=800) == 730


def test_no_reading_changes_nothing():
    assert next_limit(S, None, None, current=730, ceiling=800) == 730


def test_settings_validation():
    assert protection_errors({**S, "low_voltage": 250.0}) == {"low_voltage": "low_above_high"}
    assert protection_errors(S) == {}


class FakeCoordinator:
    def __init__(self, data, limit, user_limit):
        self.data = data
        self.power_limit = limit
        self.user_limit = user_limit
        self.commands = []

    async def async_set_power_limit(self, watts, by_user=True):
        self.commands.append((watts, by_user))
        self.power_limit = watts
        return True


def make(options, data, limit=800, user_limit=800):
    hass = SimpleNamespace()
    entry = SimpleNamespace(options=options)
    coordinator = FakeCoordinator(data, limit, user_limit)
    return hass, coordinator, OvervoltageProtection(hass, entry, coordinator)


def test_controller_sends_a_protection_command_when_enabled():
    _, coordinator, protection = make(
        {"protection": True}, {"status": 3, "ac_voltage_v": 251.0, "ac_power_w": 790}
    )
    asyncio.run(protection.async_check())
    assert coordinator.commands == [(730, False)]  # not by_user: ceiling kept
    assert "lowered to 730 W" in protection.last_action


def test_controller_idle_when_disabled_or_inverter_off():
    _, coordinator, protection = make(
        {"protection": False}, {"status": 3, "ac_voltage_v": 251.0, "ac_power_w": 790}
    )
    asyncio.run(protection.async_check())
    _, off, protection_off = make({"protection": True}, {"status": 0})
    asyncio.run(protection_off.async_check())
    assert coordinator.commands == [] and off.commands == []


def test_timer_follows_the_options():
    hass, _, protection = make({"protection": True, "interval": 60}, {})
    protection.async_apply_options()
    assert [i.total_seconds() for _, i in hass.timers] == [60]

    protection.entry.options = {"protection": True, "interval": 300}
    protection.async_apply_options()
    assert [i.total_seconds() for _, i in hass.timers] == [300]

    protection.entry.options = {"protection": False}
    protection.async_apply_options()
    assert hass.timers == []
