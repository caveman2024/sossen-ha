"""Sensor platform for SOSSEN Microinverter."""

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_DEVICE_ID,
    DOMAIN,
    SENSOR_DEFINITIONS,
    build_device_info,
)
from .coordinator import SossenCoordinator

STATUS_MAP = {
    0: "off",
    1: "alarm",
    3: "producing",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up SOSSEN sensors from a config entry."""
    coordinator: SossenCoordinator = hass.data[DOMAIN][entry.entry_id]

    channels = coordinator.model["channels"]
    entities: list[SensorEntity] = [
        (
            SossenRestoreSensor
            if sensor_def.get("restore")
            else SossenSensor
        )(coordinator, entry, sensor_def)
        for sensor_def in SENSOR_DEFINITIONS
        if sensor_def.get("min_channels", 2) <= channels
    ]
    entities.append(SossenStatusSensor(coordinator, entry))
    entities.append(SossenRawSensor(coordinator, entry))
    async_add_entities(entities)


class SossenSensor(CoordinatorEntity, SensorEntity):
    """Representation of a SOSSEN sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SossenCoordinator,
        entry: ConfigEntry,
        sensor_def: dict,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._key = sensor_def["key"]
        self._attr_unique_id = f"{entry.data[CONF_DEVICE_ID]}_{self._key}"
        self._attr_translation_key = self._key
        self._attr_device_class = sensor_def.get("device_class")
        self._attr_native_unit_of_measurement = sensor_def.get("unit")
        self._attr_state_class = sensor_def.get("state_class")
        self._attr_device_info = build_device_info(entry)
        self._attr_entity_category = sensor_def.get("entity_category")
        if "icon" in sensor_def:
            self._attr_icon = sensor_def["icon"]
        if "precision" in sensor_def:
            self._attr_suggested_display_precision = sensor_def["precision"]

    @property
    def native_value(self):
        """Return the sensor value."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._key)

    @property
    def available(self) -> bool:
        """Return True if entity is available.

        A None value means "nothing to measure" (inverter asleep at night):
        the entity is reported as unavailable rather than unknown.
        """
        if not super().available or self.coordinator.data is None:
            return False
        return self.coordinator.data.get(self._key) is not None


class SossenRestoreSensor(SossenSensor, RestoreSensor):
    """Odometer-style sensor that keeps its last value across restarts.

    Without this, the counter reads unavailable after a Home Assistant
    restart until the first data frame lands — ~10s by day, but all night
    when the inverter is powered off (no battery), since there is no
    previous value for the coordinator to retain.
    """

    _last_value = None

    async def async_added_to_hass(self) -> None:
        """Restore the last known value."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_sensor_data()) is not None:
            self._last_value = last.native_value
        elif (state := await self.async_get_last_state()) is not None:
            # First start after upgrading from a version where this was a
            # plain sensor: no sensor extra data was stored yet, but the
            # last state string was.
            try:
                self._last_value = float(state.state)
            except ValueError:
                pass  # "unavailable" / "unknown": nothing to restore

    @property
    def native_value(self):
        """Return the live value, falling back to the last known one."""
        value = super().native_value
        if value is not None:
            self._last_value = value
            return value
        return self._last_value

    @property
    def available(self) -> bool:
        """Available while there is a live or a last known value."""
        return super().available or self._last_value is not None


class SossenStatusSensor(CoordinatorEntity, SensorEntity):
    """Sensor with 3 states: producing, alarm, off."""

    _attr_has_entity_name = True
    _attr_translation_key = "status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["producing", "alarm", "off"]

    def __init__(self, coordinator: SossenCoordinator, entry: ConfigEntry) -> None:
        """Initialize the status sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.data[CONF_DEVICE_ID]}_status"
        self._attr_device_info = build_device_info(entry)

    @property
    def native_value(self) -> str | None:
        """Return the status: producing, alarm, off, or None if unmapped."""
        if not self.coordinator.data or not self.coordinator.data.get("status"):
            return "off"
        raw = self.coordinator.data.get("status", 0)
        return STATUS_MAP.get(raw)

    @property
    def icon(self) -> str:
        """Return icon based on status."""
        value = self.native_value
        if value == "producing":
            return "mdi:solar-power"
        if value == "alarm":
            return "mdi:alert"
        return "mdi:power-sleep"

    @property
    def extra_state_attributes(self):
        """Return the raw status value."""
        if not self.coordinator.data:
            return None
        return {"status_raw": self.coordinator.data.get("status", 0)}


class SossenRawSensor(CoordinatorEntity, SensorEntity):
    """Diagnostic sensor showing all raw DP values."""

    _attr_has_entity_name = True
    _attr_translation_key = "raw"
    _attr_icon = "mdi:bug"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: SossenCoordinator, entry: ConfigEntry) -> None:
        """Initialize the raw sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.data[CONF_DEVICE_ID]}_raw"
        self._attr_device_info = build_device_info(entry)

    @property
    def native_value(self):
        """Return the current AC power as the sensor state."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("ac_power_w")

    @property
    def extra_state_attributes(self):
        """Return all raw DP values as attributes."""
        if not self.coordinator.data:
            return None
        raw = self.coordinator.data.get("_raw", {})
        return {f"dp_{k}": v for k, v in raw.items()}
