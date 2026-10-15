"""Minimal stand-ins for the few Home Assistant names the coordinator imports.

Only constants and base classes are faked; the coordinator logic under test
is the integration's own code. Import this module before importing
custom_components.sossen.const or .coordinator.
"""

import sys
import types


class _Names:
    """Any attribute is its own name: SensorDeviceClass.POWER -> "POWER"."""

    def __getattr__(self, name):
        return name


class DataUpdateCoordinator:
    def __init__(self, hass, logger, name, update_interval):
        self.hass = hass
        self.logger = logger
        self.name = name
        self.update_interval = update_interval
        self.data = None

    async def async_shutdown(self):
        pass


class UpdateFailed(Exception):
    pass


def _module(name, **attrs):
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    sys.modules.setdefault(name, mod)
    return sys.modules[name]


_module("homeassistant")
_module("homeassistant.core", HomeAssistant=object)
_module("homeassistant.config_entries", ConfigEntry=object)
_module("homeassistant.const", Platform=_Names())
_module("homeassistant.components")
_module(
    "homeassistant.components.sensor",
    SensorDeviceClass=_Names(),
    SensorStateClass=_Names(),
)
_module("homeassistant.helpers")
_module("homeassistant.helpers.entity", EntityCategory=_Names())
_module(
    "homeassistant.helpers.update_coordinator",
    DataUpdateCoordinator=DataUpdateCoordinator,
    UpdateFailed=UpdateFailed,
)
