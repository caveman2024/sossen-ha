"""LAN discovery packet handling and Smart Life device mapping."""

import json
from types import SimpleNamespace

import tinytuya

import ha_stubs  # noqa: F401  (must precede the integration imports)
from custom_components.sossen.cloud import device_entry, is_sossen
from custom_components.sossen.config_flow import entry_title, form_labels
from custom_components.sossen.const import model_from_product
from custom_components.sossen.discovery import _probe_payload, parse_announcement


def announcement(payload: dict) -> bytes:
    """A Tuya v3.x UDP broadcast, encrypted like a real device sends it."""
    return tinytuya.encrypt(json.dumps(payload).encode(), tinytuya.udpkey)


def test_announcement_gives_device_id_and_ip():
    pkt = announcement({"gwId": "bf7651c8a12002d3d7kc7k", "ip": "192.168.1.38", "version": "3.5"})
    assert parse_announcement(pkt, "192.168.1.99") == ("bf7651c8a12002d3d7kc7k", "192.168.1.38")


def test_announcement_without_ip_uses_the_sender():
    pkt = announcement({"devId": "abc"})
    assert parse_announcement(pkt, "10.0.0.7") == ("abc", "10.0.0.7")


def test_garbage_is_ignored():
    assert parse_announcement(b"\x00\x01garbage", "10.0.0.7") is None
    assert parse_announcement(announcement({"foo": 1}), "10.0.0.7") is None


def test_probe_payload_is_a_tuya_frame():
    payload = _probe_payload("192.168.1.10")
    assert payload.startswith(tinytuya.PREFIX_6699_BIN)


def test_sossen_devices_are_recognised_and_mapped():
    dev = SimpleNamespace(
        id="id1", name="Terrazzo", local_key="k" * 16, product_name="SOSSEN 4in1 2400W"
    )
    other = SimpleNamespace(id="id2", name="Lamp", local_key="x", product_name="Bulb")
    assert is_sossen(dev) and not is_sossen(other)
    assert device_entry(dev) == {
        "device_id": "id1", "name": "Terrazzo", "local_key": "k" * 16, "model": "4in1",
    }


def test_model_guess():
    assert model_from_product("SOSSEN-2in1-DE") == "2in1"
    assert model_from_product("Sossen 2400-Y-N") == "4in1"
    assert model_from_product("") == "2in1"


def test_form_labels_are_unique():
    inverters = [
        {"device_id": "aaaa1111", "name": "Roof"},
        {"device_id": "bbbb2222", "name": "Roof"},
        {"device_id": "cccc3333", "name": "Garden"},
    ]
    assert form_labels(inverters) == {
        "aaaa1111": "Roof (1111)", "bbbb2222": "Roof (2222)", "cccc3333": "Garden",
    }


def test_manual_entries_keep_the_model_title():
    assert entry_title({"model": "2in1"}) == "SOSSEN 2in1 (600/800/1000W)"
    assert entry_title({"model": "2in1", "name": "Terrazzo"}) == "Terrazzo"
