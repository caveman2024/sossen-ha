"""Data update coordinator for SOSSEN Microinverter."""

import asyncio
import logging
import socket
import time
from datetime import timedelta

import tinytuya

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_IP,
    CONF_LOCAL_KEY,
    CONF_MODEL,
    CONF_POLL_INTERVAL,
    ARM_AFTER_SILENCE,
    ARM_DP19_DP,
    ARM_DP19_VALUE,
    DEFAULT_MODEL,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    DP_SET_POWER_LIMIT,
    ERR_HANDSHAKE_REFUSED,
    HANDSHAKE_REFUSALS_WARN_AFTER,
    HEARTBEAT_EVERY,
    LISTEN_WINDOW,
    MAX_FAILED_POLLS,
    MODELS,
    OFF_POLL_INTERVAL,
    RECONNECT_AFTER_FAILURES,
    SENSOR_DEFINITIONS,
    SET_LIMIT_ATTEMPTS,
    SOCKET_TIMEOUT,
    TUYA_DP_COMMAND,
    TUYA_PORT,
    WARMUP_POLLS,
)
from .protocol import build_set_power_payload, decode_payload, decode_records

_LOGGER = logging.getLogger(__name__)


def _tuya_failed(result) -> bool:
    """Return True if a tinytuya call reported a failure.

    The library signals errors with a dict carrying an "Error" key rather
    than by raising; a successful nowait call returns None.
    """
    return isinstance(result, dict) and bool(result.get("Error"))


class SossenCoordinator(DataUpdateCoordinator):
    """Coordinator to manage polling the SOSSEN inverter.

    On/off state is deduced from the network, not from the sun: the
    inverter's ESP32 keeps its WiFi/TCP stack up whenever the device has
    power (solar or battery). If the Tuya port stops accepting connections
    the inverter is powered off; if it accepts connections but stops
    answering polls, that is a real communication problem.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self._poll_interval = entry.data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=self._poll_interval),
        )
        self.entry = entry
        self._device_id = entry.data[CONF_DEVICE_ID]
        self._device_ip = entry.data[CONF_DEVICE_IP]
        self._local_key = entry.data[CONF_LOCAL_KEY]
        self._device: tinytuya.Device | None = None
        model_key = entry.data.get(CONF_MODEL, DEFAULT_MODEL)
        self.model = MODELS.get(model_key, MODELS[DEFAULT_MODEL])
        self._power_limit: int | None = entry.data.get(
            "power_limit_last", self.model["power_limit_max"]
        )
        self._limit_read_pending: bool = entry.data.get("power_limit_last") is None
        self._limit_read_attempts: int = 3
        self._failed_polls: int = 0
        # Grace period at HA start too: the device may still be warming up.
        self._warmup_polls_left: int = WARMUP_POLLS
        self.is_powered_off: bool = False
        # Re-arm the warm-up grace only once per outage (reset on the next
        # successful poll) so a wedged socket heals silently but a genuinely
        # dead device still surfaces as unavailable instead of stale data.
        self._reconnect_grace_used: bool = False
        # Push-listen state: monotonic time of the last pushed frame and
        # of the last heartbeat. _last_push=0 arms the stream immediately
        # at setup.
        self._last_push: float = 0.0
        self._last_heartbeat: float = 0.0
        # True when the last listen cycle ended because tinytuya reported no
        # session (connect/handshake failed or socket dropped), as opposed to
        # a cycle that merely heard nothing while the stream re-arms.
        self._session_lost: bool = False
        # tinytuya error code ("Err") that ended the last lost session, and
        # how many cycles in a row ended with a refused handshake.
        self._session_err: str | None = None
        self._handshake_refusals: int = 0
        # The tinytuya Device shares one socket and session state: never let
        # two executor threads (poll vs. set-limit) touch it concurrently.
        self._device_lock = asyncio.Lock()

    async def _locked_job(self, func, *args):
        """Run a device-touching sync function in the executor, serialized."""
        async with self._device_lock:
            return await self.hass.async_add_executor_job(func, *args)

    def _ensure_device(self) -> tinytuya.Device:
        """Create or return the TinyTuya device (called from executor thread)."""
        if self._device is None:
            self._device = tinytuya.Device(
                self._device_id, self._device_ip, self._local_key, version=3.5
            )
            self._device.set_socketTimeout(SOCKET_TIMEOUT)
            # set_socketTimeout caps a single attempt, NOT the retry ladder:
            # tinytuya reconnects 5 times with a 5s sleep between tries by
            # default, so one heartbeat()/receive() against an unreachable
            # inverter blocks 35s and a whole listen cycle 105s (measured).
            # Retrying is this coordinator's job — it comes back every poll
            # interval and counts silent cycles itself — so the library is
            # told to fail on the first attempt instead of duplicating it.
            self._device.set_socketRetryLimit(1)
            self._device.set_socketRetryDelay(0)
            self._device.set_socketPersistent(True)
        return self._device

    def _disconnect(self) -> None:
        """Disconnect the TinyTuya device."""
        if self._device is not None:
            try:
                self._device.close()
            except Exception:
                pass
            self._device = None

    def _sync_probe(self) -> bool:
        """Check whether the inverter is powered (TCP port reachable).

        Runs in executor thread. A powered inverter always accepts TCP
        connections on the Tuya port, even when the protocol session is
        not answering.
        """
        try:
            with socket.create_connection((self._device_ip, TUYA_PORT), timeout=5):
                return True
        except OSError:
            return False

    def _sync_listen(self) -> dict | None:
        """Listen for pushed data frames for up to LISTEN_WINDOW seconds.

        Runs in executor thread, blocking is OK. The inverter pushes the
        full data frame (DP 21, preceded by the compact DP 25) every ~5s
        once its report stream is ARMED, and stops ~1-2 min after the last
        arm/keepalive. Do NOT call updatedps() to fetch: tinytuya flushes
        the receive buffer before sending, discarding any frame pushed since
        the previous cycle — that bug is what made v1.1 see fresh data only
        every ~10 minutes.

        The arm is the vendor app's secret: it sets DP 19 to the raw value
        ARM_DP19_VALUE, which flips the device into 5s broadcast mode toward
        ALL connected sessions (recovered by decrypting a capture of the
        SmartLife app's local Tuya 3.5 traffic). We re-send it whenever the
        stream has been silent for ARM_AFTER_SILENCE, plus a heartbeat every
        HEARTBEAT_EVERY to keep the session alive. Verified live: 0 frames
        in 20s idle, then a steady 5.1s cadence within seconds of the arm.
        """
        self._session_lost = False
        self._session_err = None
        try:
            device = self._ensure_device()
            now = time.monotonic()
            if now - self._last_heartbeat >= HEARTBEAT_EVERY:
                # A nowait call returns None when it went out, an error dict
                # when the session could not be established. Bail on the
                # latter: without a session there is nothing to arm and
                # nothing can be pushed, and the arm and the first receive
                # would each re-pay the same failed 3.5 handshake — 6s a
                # piece against a device that accepts TCP but stays mute.
                result = device.heartbeat(nowait=True)
                if _tuya_failed(result):
                    _LOGGER.debug("No session (heartbeat), skipping cycle")
                    self._mark_session_lost(result)
                    return None
                self._last_heartbeat = now
            if now - self._last_push >= ARM_AFTER_SILENCE:
                _LOGGER.debug(
                    "Push stream silent for %.0fs, re-arming (DP19=%s)",
                    now - self._last_push, ARM_DP19_VALUE,
                )
                result = device.set_value(ARM_DP19_DP, ARM_DP19_VALUE, nowait=True)
                if _tuya_failed(result):
                    _LOGGER.debug("No session (arm), skipping cycle")
                    self._mark_session_lost(result)
                    return None

            latest: dict | None = None
            deadline = time.monotonic() + LISTEN_WINDOW
            while time.monotonic() < deadline:
                result = device.receive()
                if _tuya_failed(result):
                    # tinytuya reports a dead session as an error dict, and
                    # has already thrown the socket away. The deadline is
                    # only consulted between receives, so carrying on here
                    # pays another connect per iteration: 90s for a refused
                    # port, or — against a device that resets on accept —
                    # thousands of reconnects inside the 8s window.
                    _LOGGER.debug("Listen aborted: %s", result.get("Error"))
                    self._mark_session_lost(result)
                    break
                if not result or "dps" not in result:
                    continue
                for _dp_key, val in result["dps"].items():
                    if isinstance(val, str) and len(val) > 20:
                        decoded = decode_payload(val)
                        if decoded:
                            latest = decoded
            if latest is not None:
                self._last_push = time.monotonic()
            return latest
        except Exception as err:
            _LOGGER.debug("_sync_listen exception: %s", err)
            self._mark_session_lost(None)
            return None

    def _mark_session_lost(self, result: dict | None) -> None:
        """Record that the listen cycle ended without a session, and why."""
        self._session_lost = True
        self._session_err = result.get("Err") if isinstance(result, dict) else None

    def _sync_read_power_limit(self) -> int | None:
        """Read the current power limit by querying DP 24 config."""
        try:
            device = self._ensure_device()
            device.updatedps([TUYA_DP_COMMAND])
            for _ in range(5):
                result = device.receive()
                if result and "dps" in result and str(TUYA_DP_COMMAND) in result["dps"]:
                    val = result["dps"][str(TUYA_DP_COMMAND)]
                    if isinstance(val, str) and len(val) > 10:
                        records = decode_records(val)
                        if DP_SET_POWER_LIMIT in records:
                            return records[DP_SET_POWER_LIMIT]
            return None
        except Exception:
            return None

    def _sync_set_power_limit(self, watts: int) -> bool:
        """Set the power limit (runs in executor thread)."""
        payload = build_set_power_payload(watts)
        # The library retries nothing any more (see _ensure_device) and a
        # silent poll simply comes round again next cycle, but a write the
        # user asked for has no second chance, so it gets its own attempts.
        for _ in range(SET_LIMIT_ATTEMPTS):
            try:
                device = self._ensure_device()
                result = device.set_value(TUYA_DP_COMMAND, payload)
            except Exception as err:
                _LOGGER.debug("_sync_set_power_limit exception: %s", err)
                result = None
            # tinytuya returns an error dict on failure, not None
            if result is None or _tuya_failed(result):
                continue
            return True
        return False

    def _build_off_data(self) -> dict:
        """Build the data dict for a powered-off inverter.

        Per-sensor policy from SENSOR_DEFINITIONS ("when_off"):
        - zero: production really is 0 W
        - unavailable (None): nothing to measure
        - retain: odometer counters must never drop to 0 (a drop would be
          read as a meter reset by the Energy dashboard)
        """
        prev = self.data or {}
        # Empty _raw: showing the last daylight registers as current would lie.
        data: dict = {"status": 0, "_raw": {}}
        for sensor_def in SENSOR_DEFINITIONS:
            key = sensor_def["key"]
            policy = sensor_def.get("when_off", "unavailable")
            if policy == "zero":
                data[key] = 0
            elif policy == "retain":
                data[key] = prev.get(key)
            else:
                data[key] = None
        return data

    def _enter_powered_off(self) -> None:
        """Switch to low-frequency probing while the inverter has no power."""
        if not self.is_powered_off:
            _LOGGER.info(
                "Inverter unreachable: powered off, probing every %ss",
                OFF_POLL_INTERVAL,
            )
            self.is_powered_off = True
            self.update_interval = timedelta(seconds=OFF_POLL_INTERVAL)

    async def _async_update_data(self) -> dict:
        """Fetch data from the inverter, deducing on/off from the network."""
        if self.is_powered_off:
            # Only a light TCP probe while off — no full protocol attempts.
            if not await self.hass.async_add_executor_job(self._sync_probe):
                return self._build_off_data()
            _LOGGER.info("Inverter powered back on, resuming polling")
            self.is_powered_off = False
            self._failed_polls = 0
            self._reconnect_grace_used = False
            # Grace period: after power-on the device needs ~2 min of
            # established connection before it answers polls.
            self._warmup_polls_left = WARMUP_POLLS
            self.update_interval = timedelta(seconds=self._poll_interval)
            # Fall through and try a real poll right away.

        # Once a cycle has lost its session, check the TCP port before
        # talking protocol again: the probe answers in milliseconds when the
        # inverter is powered and costs one timeout when it is not, whereas
        # a full listen cycle pays a connect for the heartbeat, one for the
        # arm and one for the first receive. Only after a lost session, not
        # after mere silence: 30-120s without frames is normal while the
        # stream re-arms, and probing every silent cycle would open a second
        # socket next to the live session exactly while it needs to stay
        # undisturbed.
        if self._session_lost and not await self.hass.async_add_executor_job(
            self._sync_probe
        ):
            data = None
            self._session_err = None
        else:
            data = await self._locked_job(self._sync_listen)

        # A refused handshake is what a wrong Local Key looks like, but a
        # booting inverter can refuse it too, so only a persistent run of
        # them is reported — once per outage, at warning level, because at
        # debug level a wrong key just reads as "reachable but not answering".
        if data is None and self._session_err == ERR_HANDSHAKE_REFUSED:
            self._handshake_refusals += 1
            if self._handshake_refusals == HANDSHAKE_REFUSALS_WARN_AFTER:
                _LOGGER.warning(
                    "Inverter session refused (Tuya error %s) for %d cycles in "
                    "a row: the Local Key is probably wrong (it changes every "
                    "time the inverter is re-added in the app)",
                    ERR_HANDSHAKE_REFUSED, self._handshake_refusals,
                )
        else:
            self._handshake_refusals = 0

        if data is not None:
            self._failed_polls = 0
            self._warmup_polls_left = 0
            self._reconnect_grace_used = False
            _LOGGER.debug("Got data: AC power=%sW", data.get("ac_power_w"))
            # Read power limit once after first successful poll, but give up
            # after a few attempts (the query may never be answered) so the
            # poll cycle doesn't pay the extra round-trip forever.
            if self._limit_read_pending:
                limit = await self._locked_job(self._sync_read_power_limit)
                if limit is not None:
                    self._power_limit = limit
                    self._limit_read_pending = False
                    _LOGGER.info("Read power limit from device: %dW", limit)
                else:
                    self._limit_read_attempts -= 1
                    if self._limit_read_attempts <= 0:
                        self._limit_read_pending = False
                        _LOGGER.debug(
                            "Device did not answer power limit query, "
                            "using default %sW", self._power_limit
                        )
            return data

        self._failed_polls += 1

        # A socket that answered before and then went silent is usually
        # wedged (device rebooted, session dropped, DHCP change). Tear it
        # down and give the rebuilt socket the same warm-up grace a cold
        # device gets, so it heals without flipping the sensors to
        # "unavailable". Only reconnect while NOT already warming up: the
        # fresh socket needs ~2 min of *stable* connection before the device
        # answers, so reconnecting mid-warm-up would keep it from ever
        # establishing. The grace is re-armed only once per outage
        # (self._reconnect_grace_used, reset on success) so a genuinely dead
        # device still surfaces as unavailable once the grace runs out.
        if (
            self._warmup_polls_left == 0
            and self._failed_polls >= RECONNECT_AFTER_FAILURES
        ):
            _LOGGER.debug("Forcing reconnect after %d failed polls", self._failed_polls)
            await self._locked_job(self._disconnect)
            if not self._reconnect_grace_used:
                self._warmup_polls_left = WARMUP_POLLS
                self._reconnect_grace_used = True

        # Tolerate brief blips before deciding anything.
        if self._failed_polls < MAX_FAILED_POLLS:
            if self.data is not None:
                return self.data
            return self._build_off_data()

        # Persistent silence: is the device powered at all?
        if await self.hass.async_add_executor_job(self._sync_probe):
            if self._warmup_polls_left > 0:
                # Reachable but still booting: keep reporting "off"
                # quietly instead of flagging an error.
                self._warmup_polls_left -= 1
                return self.data if self.data is not None else self._build_off_data()
            # Powered but not answering: a real communication problem.
            raise UpdateFailed(
                f"Inverter reachable but not answering after "
                f"{self._failed_polls} polls"
            )

        # TCP port unreachable: the inverter has no power (night without
        # battery, or battery empty). Not an error.
        await self._locked_job(self._disconnect)
        self._enter_powered_off()
        return self._build_off_data()

    async def async_set_power_limit(self, watts: int) -> None:
        """Set the inverter power limit."""
        success = await self._locked_job(self._sync_set_power_limit, watts)
        if success:
            self._power_limit = watts
            # Persist last set value so it survives restarts
            new_data = {**self.entry.data, "power_limit_last": watts}
            self.hass.config_entries.async_update_entry(self.entry, data=new_data)
            _LOGGER.info("Power limit set to %dW", watts)
        else:
            _LOGGER.error("Failed to set power limit to %dW", watts)

    async def async_shutdown(self) -> None:
        """Disconnect on shutdown."""
        await super().async_shutdown()
        await self._locked_job(self._disconnect)

    @property
    def power_limit(self) -> int | None:
        """Return the last set power limit."""
        return self._power_limit
