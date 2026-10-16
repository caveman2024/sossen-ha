"""Switch platform for SOSSEN Microinverter (overvoltage protection)."""

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DEVICE_ID, CONF_PROTECTION, DOMAIN, build_device_info
from .coordinator import SossenCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the protection switch."""
    coordinator: SossenCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SossenProtectionSwitch(coordinator, entry)])


class SossenProtectionSwitch(CoordinatorEntity, SwitchEntity):
    """Turn the built-in overvoltage protection on or off.

    The thresholds and steps are set in the integration's options; this
    stores only the on/off flag there, so both always agree.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "overvoltage_protection"
    _attr_icon = "mdi:flash-alert"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: SossenCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.data[CONF_DEVICE_ID]}_overvoltage_protection"
        self._attr_device_info = build_device_info(entry)

    @property
    def available(self) -> bool:
        """A setting: always available, even with the inverter off."""
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.protection.enabled

    @property
    def extra_state_attributes(self):
        return {"last_action": self.coordinator.protection.last_action}

    async def async_turn_on(self, **kwargs) -> None:
        self._set(True)

    async def async_turn_off(self, **kwargs) -> None:
        self._set(False)

    def _set(self, value: bool) -> None:
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, CONF_PROTECTION: value}
        )
