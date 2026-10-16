"""Sensor entity tests: which sensors restore, and how restored values behave."""

import asyncio
from types import SimpleNamespace

import ha_stubs  # noqa: F401  (must precede the integration imports)
from ha_stubs import SensorExtraStoredData
from custom_components.sossen import sensor as S
from custom_components.sossen.const import SENSOR_DEFINITIONS

ENTRY = SimpleNamespace(data={"device_id": "dev1"})
ENERGY_DEF = next(d for d in SENSOR_DEFINITIONS if d["key"] == "energy_total_kwh")


def make_entities(channels=2):
    coordinator = SimpleNamespace(
        data=None, last_update_success=True, model={"channels": channels}
    )
    hass = SimpleNamespace(data={S.DOMAIN: {"e1": coordinator}})
    added = []
    asyncio.run(
        S.async_setup_entry(hass, SimpleNamespace(entry_id="e1", data=ENTRY.data), added.extend)
    )
    return coordinator, {e._key: e for e in added if hasattr(e, "_key")}


def restored_energy(stored_value):
    coordinator = SimpleNamespace(data=None, last_update_success=True)
    entity = S.SossenRestoreSensor(coordinator, ENTRY, ENERGY_DEF)
    entity.stored = SensorExtraStoredData(stored_value, "kWh")
    asyncio.run(entity.async_added_to_hass())
    return coordinator, entity


def test_only_the_energy_odometer_restores():
    _, entities = make_entities()
    restoring = {k for k, e in entities.items() if isinstance(e, S.SossenRestoreSensor)}
    # Derived lifetime values are not restored: a firmware reporting them
    # wrongly would otherwise keep a bogus value forever.
    assert restoring == {"energy_total_kwh"}


def test_channel_3_4_sensors_only_on_4in1():
    _, two = make_entities(channels=2)
    _, four = make_entities(channels=4)
    assert "dc_power_3_w" not in two
    assert {"dc_power_3_w", "dc_power_4_w"} <= four.keys()


def test_restored_value_shown_until_first_frame():
    """HA restarted at night, inverter off: no live value at all."""
    coordinator, entity = restored_energy(1280.1)

    assert entity.available
    assert entity.native_value == 1280.1

    coordinator.data = {"energy_total_kwh": 1280.4}
    assert entity.native_value == 1280.4


def test_last_live_value_kept_when_frame_lacks_energy():
    coordinator, entity = restored_energy(1280.1)
    coordinator.data = {"energy_total_kwh": 1281.0}
    assert entity.native_value == 1281.0

    coordinator.data = {"energy_total_kwh": None}
    assert entity.available
    assert entity.native_value == 1281.0


def test_first_start_after_upgrade_restores_from_last_state():
    """Upgrading from a plain sensor: only the state string was stored."""
    coordinator = SimpleNamespace(data=None, last_update_success=True)
    entity = S.SossenRestoreSensor(coordinator, ENTRY, ENERGY_DEF)
    entity.stored = None
    entity.stored_state = SimpleNamespace(state="1280.6")
    asyncio.run(entity.async_added_to_hass())

    assert entity.available
    assert entity.native_value == 1280.6


def test_unavailable_last_state_is_not_restored():
    coordinator = SimpleNamespace(data=None, last_update_success=True)
    entity = S.SossenRestoreSensor(coordinator, ENTRY, ENERGY_DEF)
    entity.stored = None
    entity.stored_state = SimpleNamespace(state="unavailable")
    asyncio.run(entity.async_added_to_hass())

    assert not entity.available


def test_nothing_stored_behaves_like_a_plain_sensor():
    coordinator, entity = restored_energy(None)
    entity.stored = None
    asyncio.run(entity.async_added_to_hass())

    assert not entity.available
    coordinator.data = {"energy_total_kwh": 5.0}
    assert entity.available and entity.native_value == 5.0
