"""Diagnostics redaction and the stale-entity cleanup."""

import asyncio
from types import SimpleNamespace

import ha_stubs  # noqa: F401  (must precede the integration imports)

import sys
import types

# __init__ imports the entity registry helper: a stand-in is enough here.
sys.modules.setdefault(
    "homeassistant.helpers.entity_registry",
    types.ModuleType("homeassistant.helpers.entity_registry"),
)
sys.modules["homeassistant.helpers.entity_registry"].RegistryEntry = object
sys.modules["homeassistant.helpers"].entity_registry = sys.modules[
    "homeassistant.helpers.entity_registry"
]

import importlib.util  # noqa: E402
from pathlib import Path  # noqa: E402

# conftest deliberately skips the package __init__; load it on its own.
_spec = importlib.util.spec_from_file_location(
    "custom_components.sossen.__init__",
    Path(__file__).parent.parent / "custom_components" / "sossen" / "__init__.py",
)
_init = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_init)
_is_stale = _init._is_stale

from custom_components.sossen.diagnostics import (  # noqa: E402
    async_get_config_entry_diagnostics,
)


def test_diagnostics_never_contain_the_local_key():
    entry = SimpleNamespace(
        entry_id="e1",
        data={"device_id": "dev1", "local_key": "Mx~secret", "device_ip": "192.168.1.38"},
        options={"protection": True},
    )
    hass = SimpleNamespace(data={})
    result = asyncio.run(async_get_config_entry_diagnostics(hass, entry))

    assert result["data"]["local_key"] == "**REDACTED**"
    assert result["data"]["device_ip"] == "192.168.1.38"
    assert "Mx~secret" not in repr(result)


def test_only_the_old_switch_is_cleaned_up():
    old_switch = SimpleNamespace(domain="switch", unique_id="dev1_daytime_only")
    new_switch = SimpleNamespace(domain="switch", unique_id="dev1_overvoltage_protection")
    old_status = SimpleNamespace(domain="binary_sensor", unique_id="dev1_status")
    sensor = SimpleNamespace(domain="sensor", unique_id="dev1_ac_power_w")

    assert _is_stale(old_switch) and _is_stale(old_status)
    assert not _is_stale(new_switch) and not _is_stale(sensor)
