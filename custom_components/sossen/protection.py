"""Overvoltage protection: lower the power limit before the grid trips.

The inverter disconnects from the grid around 253 V, and the voltage it
measures rises with what it injects. Every interval its AC voltage decides:
at or above the high threshold the limit goes down one step, at or below the
low threshold it goes back up one smaller step, in between nothing changes —
down fast, up slowly, so it does not oscillate. It never goes below the
configured floor nor above the limit the user chose, and a command is sent
only when the limit actually changes (each may be written to flash).

The limit only goes back up while the inverter is actually held by it (its
AC output within PROTECTION_RAISE_MARGIN of the limit): raising it while the
inverter produces well below (clouds, evening) changes nothing but costs a
command.

Algorithm adapted from ritonbrunis-lab/sossen-direct (MIT).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CONF_HIGH_VOLTAGE,
    CONF_INTERVAL,
    CONF_LOW_VOLTAGE,
    CONF_MIN_LIMIT,
    CONF_PROTECTION,
    CONF_STEP_DOWN,
    CONF_STEP_UP,
    PROTECTION_DEFAULTS,
    PROTECTION_RAISE_MARGIN,
)

_LOGGER = logging.getLogger(__name__)


def protection_settings(options: dict) -> dict:
    """Return the protection settings with defaults filled in."""
    return {key: options.get(key, default) for key, default in PROTECTION_DEFAULTS.items()}


def protection_errors(values: dict) -> dict[str, str]:
    """Inconsistent settings, as {field: translation key}."""
    if values[CONF_LOW_VOLTAGE] >= values[CONF_HIGH_VOLTAGE]:
        return {CONF_LOW_VOLTAGE: "low_above_high"}
    return {}


def next_limit(
    settings: dict,
    voltage: float | None,
    ac_power: float | None,
    current: int,
    ceiling: int,
) -> int:
    """Return the limit the protection wants (== current: no command).

    voltage/ac_power are the inverter's live readings (None: no data, do
    nothing); ceiling is the limit the user chose.
    """
    if voltage is None:
        return current
    floor = min(int(settings[CONF_MIN_LIMIT]), ceiling)
    if voltage >= settings[CONF_HIGH_VOLTAGE]:
        target = current - int(settings[CONF_STEP_DOWN])
    elif voltage <= settings[CONF_LOW_VOLTAGE]:
        held = ac_power is None or ac_power >= current - PROTECTION_RAISE_MARGIN
        if not held:
            return current
        target = current + int(settings[CONF_STEP_UP])
    else:
        return current
    return max(floor, min(ceiling, target))


class OvervoltageProtection:
    """Periodically apply next_limit() to one inverter."""

    def __init__(self, hass: HomeAssistant, entry, coordinator) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.last_action: str | None = None
        self._unsub: CALLBACK_TYPE | None = None
        self._interval: int | None = None

    @property
    def settings(self) -> dict:
        return protection_settings(self.entry.options)

    @property
    def enabled(self) -> bool:
        return bool(self.settings[CONF_PROTECTION])

    @callback
    def async_apply_options(self) -> None:
        """(Re)start or stop the timer after the options changed."""
        interval = int(self.settings[CONF_INTERVAL])
        if not self.enabled:
            self.async_stop()
        elif self._unsub is None or interval != self._interval:
            self.async_stop()
            self._interval = interval
            self._unsub = async_track_time_interval(
                self.hass, self.async_check, timedelta(seconds=interval)
            )
            _LOGGER.info("Overvoltage protection active, every %ss", interval)

    @callback
    def async_stop(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    async def async_check(self, _now=None) -> None:
        """One regulation step."""
        if not self.enabled:
            return
        coordinator = self.coordinator
        data = coordinator.data or {}
        if not data.get("status"):
            return  # off or no live frame: nothing to regulate
        current = coordinator.power_limit or coordinator.user_limit
        target = next_limit(
            self.settings,
            data.get("ac_voltage_v"),
            data.get("ac_power_w"),
            current,
            coordinator.user_limit,
        )
        if target == current:
            return
        if await coordinator.async_set_power_limit(target, by_user=False):
            verb = "lowered" if target < current else "raised"
            self.last_action = f"{verb} to {target} W at {data['ac_voltage_v']} V"
            _LOGGER.info("Overvoltage protection: %s", self.last_action)
