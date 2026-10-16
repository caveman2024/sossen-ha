"""Find an inverter's LAN address from its Device ID and Local Key.

Two mechanisms, both keyed by Device ID (adapted from
ritonbrunis-lab/sossen-direct, MIT):

1. Broadcasts: Tuya devices announce {"gwId", "ip", ...} on UDP 6666/6667,
   and v3.5 devices answer a REQ_DEVINFO probe on UDP 7000. Ports another
   Tuya integration already holds are shared (SO_REUSEPORT) or skipped.
2. Subnet sweep, setup wizard only and only when broadcasts found nothing:
   connect to TCP 6668 on every host of the local /24 networks and identify
   the inverter by a successful v3.5 handshake with its Local Key (a wrong
   key fails it). It briefly opens a session on other Tuya devices too,
   so it never runs in the background.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import socket
from collections.abc import Callable

import tinytuya

from homeassistant.core import HomeAssistant

from .const import (
    DISCOVERY_PORTS,
    DISCOVERY_PROBE_PORT,
    LOCATE_PROBE_EVERY,
    TUYA_PORT,
)

_LOGGER = logging.getLogger(__name__)

# Wi-Fi devices in power save can take over a second to answer a SYN.
SWEEP_CONNECT_TIMEOUT = 2.0
SWEEP_CONCURRENCY = 64


def parse_announcement(data: bytes, sender: str) -> tuple[str, str] | None:
    """Return (device id, ip) from a Tuya UDP broadcast, or None."""
    try:
        info = json.loads(tinytuya.decrypt_udp(data))
    except Exception:  # noqa: BLE001 — anything undecodable is not for us
        return None
    if not isinstance(info, dict):
        return None
    dev_id = info.get("gwId") or info.get("devId")
    if not dev_id:
        return None
    return dev_id, info.get("ip") or sender


def _probe_payload(own_ip: str) -> bytes:
    """Build the REQ_DEVINFO broadcast that makes v3.5 devices announce."""
    body = json.dumps({"from": "app", "ip": own_ip}).encode()
    msg = tinytuya.TuyaMessage(
        0, tinytuya.REQ_DEVINFO, None, body, 0, True, tinytuya.PREFIX_6699_VALUE, True
    )
    return tinytuya.pack_message(msg, hmac_key=tinytuya.udpkey)


def _handshake_ok(device_id: str, local_key: str, host: str) -> bool:
    """Return True if host answers a v3.5 session handshake with this key."""
    dev = tinytuya.Device(device_id, host, local_key, version=3.5)
    dev.set_socketTimeout(3)
    dev.set_socketRetryLimit(1)
    try:
        return dev._get_socket(True) is True  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return False
    finally:
        with contextlib.suppress(Exception):
            dev.close()


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, on_packet: Callable[[bytes, str], None]) -> None:
        self._on_packet = on_packet

    def datagram_received(self, data: bytes, addr) -> None:
        self._on_packet(data, addr[0])


class _Locator:
    """One search for a set of inverters: listen, probe, maybe sweep."""

    def __init__(self, hass: HomeAssistant, keys: dict[str, str]) -> None:
        self.hass = hass
        self.keys = keys
        self.found: dict[str, str] = {}
        self._transports: list[asyncio.DatagramTransport] = []

    def missing(self) -> list[str]:
        return [dev_id for dev_id in self.keys if dev_id not in self.found]

    def _on_packet(self, data: bytes, sender: str) -> None:
        parsed = parse_announcement(data, sender)
        if parsed and parsed[0] in self.keys and parsed[0] not in self.found:
            _LOGGER.info("Inverter %s found at %s (broadcast)", *parsed)
            self.found[parsed[0]] = parsed[1]

    async def listen(self) -> None:
        loop = asyncio.get_running_loop()
        for port in DISCOVERY_PORTS:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                if hasattr(socket, "SO_REUSEPORT"):
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.bind(("", port))
                sock.setblocking(False)
            except OSError as err:
                sock.close()
                _LOGGER.debug("UDP %s unavailable for discovery: %s", port, err)
                continue
            transport, _ = await loop.create_datagram_endpoint(
                lambda: _Protocol(self._on_packet), sock=sock
            )
            self._transports.append(transport)

    def close(self) -> None:
        for transport in self._transports:
            transport.close()
        self._transports.clear()

    async def networks(self) -> list[tuple[str, ipaddress.IPv4Network]]:
        from homeassistant.components import network

        result = []
        for adapter in await network.async_get_adapters(self.hass):
            if not adapter["enabled"]:
                continue
            for ip_info in adapter["ipv4"]:
                own = ip_info["address"]
                prefix = max(int(ip_info["network_prefix"]), 24)
                net = ipaddress.IPv4Network(f"{own}/{prefix}", strict=False)
                if not net.is_loopback:
                    result.append((own, net))
        return result

    async def probe(self) -> None:
        for own, net in await self.networks():
            payload = _probe_payload(own)
            for target in (str(net.broadcast_address), "255.255.255.255"):
                try:
                    await self.hass.async_add_executor_job(
                        _send_udp, own, target, payload
                    )
                except OSError as err:
                    _LOGGER.debug("Probe %s -> %s failed: %s", own, target, err)

    async def sweep(self) -> None:
        hosts: list[str] = []
        for own, net in await self.networks():
            hosts += [str(h) for h in net.hosts() if str(h) != own]
        sem = asyncio.Semaphore(SWEEP_CONCURRENCY)

        async def is_open(host: str) -> str | None:
            async with sem:
                try:
                    _, writer = await asyncio.wait_for(
                        asyncio.open_connection(host, TUYA_PORT),
                        SWEEP_CONNECT_TIMEOUT,
                    )
                except (OSError, asyncio.TimeoutError):
                    return None
                writer.close()
                return host

        candidates = [h for h in await asyncio.gather(*map(is_open, hosts)) if h]
        _LOGGER.info(
            "Sweep: port %s open on %s", TUYA_PORT, ", ".join(candidates) or "no host"
        )
        for dev_id in self.missing():
            for host in list(candidates):
                if await self.hass.async_add_executor_job(
                    _handshake_ok, dev_id, self.keys[dev_id], host
                ):
                    _LOGGER.info("Inverter %s found at %s (sweep)", dev_id, host)
                    self.found[dev_id] = host
                    candidates.remove(host)
                    break


def _send_udp(own: str, target: str, payload: bytes) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind((own, 0))
        sock.sendto(payload, (target, DISCOVERY_PROBE_PORT))


async def async_locate(
    hass: HomeAssistant,
    keys: dict[str, str],
    timeout: float,
    sweep: bool = False,
) -> dict[str, str]:
    """Search for inverters (Device ID -> Local Key) for up to timeout s.

    Broadcasts first; the subnet sweep only if sweep is True and some
    inverter was not heard within the first probes. Returns Device ID -> IP
    for the inverters found.
    """
    locator = _Locator(hass, keys)
    await locator.listen()
    try:
        async with asyncio.timeout(timeout):
            for attempt in range(3):
                await locator.probe()
                await asyncio.sleep(LOCATE_PROBE_EVERY)
                if not locator.missing():
                    break
            if sweep and locator.missing():
                await locator.sweep()
            while locator.missing():
                await locator.probe()
                await asyncio.sleep(LOCATE_PROBE_EVERY)
    except TimeoutError:
        pass
    finally:
        locator.close()
    return locator.found
