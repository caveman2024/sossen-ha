"""SOSSEN Microinverter integration for Home Assistant."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, PLATFORMS
from .coordinator import SossenCoordinator
from .protection import OvervoltageProtection

_LOGGER = logging.getLogger(__name__)

# Entities of older versions since removed: the status sensor that was
# wrongly registered under binary_sensor, and the daytime-only switch
# (dropped in 1.1.0). Matched by unique id suffix for switches, since the
# overvoltage protection switch (1.6.0) lives in the same domain.
_STALE_DOMAINS = ("binary_sensor",)
_STALE_UNIQUE_ID_SUFFIXES = ("_daytime_only",)


def _is_stale(entity: er.RegistryEntry) -> bool:
    return entity.domain in _STALE_DOMAINS or entity.unique_id.endswith(
        _STALE_UNIQUE_ID_SUFFIXES
    )


def _cleanup_stale_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove registry entities left over from older versions."""
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if _is_stale(entity):
            _LOGGER.info("Removing stale entity %s", entity.entity_id)
            registry.async_remove(entity.entity_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up SOSSEN from a config entry."""
    _LOGGER.info("SOSSEN setup starting")
    hass.data.setdefault(DOMAIN, {})

    _cleanup_stale_entities(hass, entry)

    coordinator = SossenCoordinator(hass, entry)
    coordinator.protection = OvervoltageProtection(hass, entry, coordinator)

    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Deliberately not async_config_entry_first_refresh(): one cycle talks to
    # the inverter, and there is nothing to wait for. An inverter that is off
    # (night, no battery) is not an error here — the coordinator reports it as
    # "off" rather than failing — so blocking on that first cycle only holds
    # the entities back and makes HA log "Waiting for integrations to complete
    # setup" for as long as the device stays quiet. The entities read None
    # until the first frame lands, which is what they already do at night.
    entry.async_create_background_task(
        hass, coordinator.async_refresh(), f"{DOMAIN}-first-refresh"
    )

    # Options (overvoltage protection) apply live, without a reload. The
    # listener also fires on data updates (limit persisted, IP moved),
    # which async_apply_options shrugs off.
    coordinator.protection.async_apply_options()
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    entry.async_on_unload(coordinator.protection.async_stop)

    _LOGGER.info(
        "SOSSEN setup complete, polling every %ss",
        coordinator.update_interval.total_seconds(),
    )
    return True


async def _async_entry_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply changed options and refresh the entities that show them."""
    coordinator: SossenCoordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.protection.async_apply_options()
    coordinator.async_update_listeners()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: SossenCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
    return unload_ok
