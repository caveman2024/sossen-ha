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


# Entity hierarchy, shaped like Home Assistant's so the integration's
# multiple inheritance (and its super() chain) resolves the same way.
class Entity:
    async def async_added_to_hass(self):
        pass


class CoordinatorEntity(Entity):
    def __init__(self, coordinator):
        self.coordinator = coordinator

    @property
    def available(self):
        return self.coordinator.last_update_success


class SensorEntity(Entity):
    pass


class RestoreEntity(Entity):
    # Tests set this to the State HA would have stored before the restart.
    stored_state = None

    async def async_added_to_hass(self):
        await super().async_added_to_hass()

    async def async_get_last_state(self):
        return self.stored_state


class SensorExtraStoredData:
    def __init__(self, native_value, native_unit_of_measurement=None):
        self.native_value = native_value
        self.native_unit_of_measurement = native_unit_of_measurement


class RestoreSensor(SensorEntity, RestoreEntity):
    # Tests set this to what HA would have stored before the restart.
    stored: SensorExtraStoredData | None = None

    async def async_get_last_sensor_data(self):
        return self.stored


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
    SensorEntity=SensorEntity,
    RestoreSensor=RestoreSensor,
)
_module("homeassistant.helpers")
_module("homeassistant.helpers.entity", EntityCategory=_Names(), Entity=Entity)
_module("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
_module(
    "homeassistant.helpers.update_coordinator",
    DataUpdateCoordinator=DataUpdateCoordinator,
    UpdateFailed=UpdateFailed,
    CoordinatorEntity=CoordinatorEntity,
)
