"""Diagnostics ("Download diagnostics"), with the Local Key redacted.

Gives users a single file to attach to an issue instead of a debug log.
Idea from ritonbrunis-lab/sossen-direct (MIT).
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_LOCAL_KEY, DOMAIN

TO_REDACT = {CONF_LOCAL_KEY}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return the entry, its options and the inverter's live state."""
    result: dict[str, Any] = {
        "data": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
    }
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is None:
        return result
    data = coordinator.data or {}
    result["runtime"] = {
        "model": coordinator.model,
        "powered_off": coordinator.is_powered_off,
        "last_update_success": coordinator.last_update_success,
        "failed_polls": coordinator._failed_polls,  # noqa: SLF001
        "session_lost": coordinator._session_lost,  # noqa: SLF001
        "session_error": coordinator._session_err,  # noqa: SLF001
        "handshake_refusals": coordinator._handshake_refusals,  # noqa: SLF001
        "power_limit": coordinator.power_limit,
        "user_limit": coordinator.user_limit,
        "limit_commands": coordinator.limit_commands,
        "protection": {
            "enabled": coordinator.protection.enabled,
            "last_action": coordinator.protection.last_action,
        },
        "values": {k: v for k, v in data.items() if k != "_raw"},
        "raw_registers": data.get("_raw", {}),
    }
    return result
