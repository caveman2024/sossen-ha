"""Config flow for SOSSEN Microinverter.

Two ways in:
- Smart Life login (recommended): user code -> QR scan -> pick inverters ->
  network search -> one entry per inverter. No key or IP to type.
- Manual: Device ID + Local Key (+ IP, optional: found on the network
  when left empty).

Options: the overvoltage protection settings.
QR login and network search adapted from ritonbrunis-lab/sossen-direct (MIT).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_IP,
    CONF_HIGH_VOLTAGE,
    CONF_INTERVAL,
    CONF_LOCAL_KEY,
    CONF_LOW_VOLTAGE,
    CONF_MIN_LIMIT,
    CONF_MODEL,
    CONF_NAME,
    CONF_POLL_INTERVAL,
    CONF_PROTECTION,
    CONF_STEP_DOWN,
    CONF_STEP_UP,
    CONF_USER_CODE,
    DEFAULT_MODEL,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    LOCATE_TIMEOUT,
    MODELS,
    POWER_LIMIT_STEP,
)
from .discovery import async_locate
from .protection import protection_errors, protection_settings

_LOGGER = logging.getLogger(__name__)

CONF_INVERTERS = "inverters"

MANUAL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_MODEL, default=DEFAULT_MODEL): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=list(MODELS),
                mode=selector.SelectSelectorMode.DROPDOWN,
                translation_key="model",
            )
        ),
        vol.Required(CONF_DEVICE_ID): str,
        vol.Required(CONF_LOCAL_KEY): str,
        vol.Optional(CONF_DEVICE_IP, default=""): str,
        vol.Optional(CONF_POLL_INTERVAL, default=DEFAULT_POLL_INTERVAL): vol.All(
            vol.Coerce(int), vol.Range(min=5, max=60)
        ),
    }
)


def _qr_schema(qr_code: str) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional("qr"): selector.QrCodeSelector(
                selector.QrCodeSelectorConfig(
                    data=f"tuyaSmart--qrLogin?token={qr_code}",
                    scale=5,
                    error_correction_level=selector.QrErrorCorrectionLevel.QUARTILE,
                )
            )
        }
    )


def entry_title(data: dict) -> str:
    """Entry title: the Smart Life name, else the model."""
    model = MODELS.get(data.get(CONF_MODEL, DEFAULT_MODEL), MODELS[DEFAULT_MODEL])
    return data.get(CONF_NAME) or f"SOSSEN {model['model']}"


def form_labels(inverters: list[dict]) -> dict[str, str]:
    """Device ID -> unique form label (the name, plus the ID end if repeated)."""
    titles = [entry_title(i) for i in inverters]
    return {
        i[CONF_DEVICE_ID]: title
        if titles.count(title) == 1
        else f"{title} ({i[CONF_DEVICE_ID][-4:]})"
        for i, title in zip(inverters, titles)
    }


class SossenConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SOSSEN."""

    VERSION = 1

    def __init__(self) -> None:
        self._login = None
        self._inverters: list[dict] = []  # from the account, not yet set up
        self._chosen: list[dict] = []
        self._manual: dict[str, Any] = {}
        self._locate_task: asyncio.Task | None = None
        self._located: dict[str, str] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return SossenOptionsFlow(config_entry)

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        """Choose how to set up."""
        return self.async_show_menu(step_id="user", menu_options=["smart_life", "manual"])

    # --- Smart Life login ---------------------------------------------------

    async def async_step_smart_life(self, user_input=None) -> ConfigFlowResult:
        """Ask for the Smart Life user code, then request a QR code."""
        errors: dict[str, str] = {}
        placeholders = {"msg": ""}
        if user_input is not None:
            from .cloud import QrLogin

            self._login = await self.hass.async_add_executor_job(QrLogin)
            try:
                ok, response = await self.hass.async_add_executor_job(
                    self._login.request_qr, user_input[CONF_USER_CODE].strip()
                )
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Smart Life QR request failed")
                ok, response = False, {}
            if ok:
                return await self.async_step_scan()
            errors["base"] = "login_error"
            placeholders["msg"] = str(response.get("msg", ""))
        return self.async_show_form(
            step_id="smart_life",
            data_schema=vol.Schema({vol.Required(CONF_USER_CODE): str}),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_scan(self, user_input=None) -> ConfigFlowResult:
        """Show the QR code; on submit, list the account's inverters."""
        if user_input is None:
            return self.async_show_form(
                step_id="scan", data_schema=_qr_schema(self._login.qr_code)
            )
        try:
            ok, result = await self.hass.async_add_executor_job(
                self._login.fetch_inverters
            )
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Could not list the Smart Life devices")
            return self.async_abort(reason="cloud_error")
        if not ok:
            # Not scanned (or expired): show a fresh code.
            await self.hass.async_add_executor_job(
                self._login.request_qr, self._login.user_code
            )
            return self.async_show_form(
                step_id="scan",
                data_schema=_qr_schema(self._login.qr_code),
                errors={"base": "scan_error"},
            )
        if not result:
            return self.async_abort(reason="no_inverters")
        configured = {
            entry.unique_id for entry in self._async_current_entries(include_ignore=False)
        }
        self._inverters = [i for i in result if i[CONF_DEVICE_ID] not in configured]
        if not self._inverters:
            return self.async_abort(reason="already_configured")
        return await self.async_step_pick()

    async def async_step_pick(self, user_input=None) -> ConfigFlowResult:
        """Choose the inverters to add (all by default)."""
        if user_input is not None:
            chosen = set(user_input[CONF_INVERTERS])
            self._chosen = [i for i in self._inverters if i[CONF_DEVICE_ID] in chosen]
            if self._chosen:
                return await self.async_step_locate()
        labels = form_labels(self._inverters)
        options = [
            selector.SelectOptionDict(value=dev_id, label=label)
            for dev_id, label in labels.items()
        ]
        return self.async_show_form(
            step_id="pick",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_INVERTERS, default=[o["value"] for o in options]
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options, multiple=True)
                    )
                }
            ),
            description_placeholders={"count": str(len(self._inverters))},
        )

    # --- Network search (both paths) ---------------------------------------

    async def async_step_locate(self, user_input=None) -> ConfigFlowResult:
        """Search the network for the chosen inverters (up to ~45 s)."""
        if self._locate_task is None:
            keys = {i[CONF_DEVICE_ID]: i[CONF_LOCAL_KEY] for i in self._chosen}
            self._locate_task = self.hass.async_create_task(
                async_locate(self.hass, keys, LOCATE_TIMEOUT, sweep=True)
            )
        if not self._locate_task.done():
            return self.async_show_progress(
                step_id="locate",
                progress_action="locate",
                progress_task=self._locate_task,
                description_placeholders={"seconds": str(LOCATE_TIMEOUT)},
            )
        try:
            self._located = self._locate_task.result()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Inverter search failed")
            self._located = {}
        self._locate_task = None
        return self.async_show_progress_done(next_step_id="addresses")

    async def async_step_addresses(self, user_input=None) -> ConfigFlowResult:
        """Ask the IP of every inverter the search did not find."""
        labels = form_labels(self._chosen)
        missing = [i for i in self._chosen if i[CONF_DEVICE_ID] not in self._located]
        if user_input is not None:
            for inverter in missing:
                ip = (user_input.get(labels[inverter[CONF_DEVICE_ID]]) or "").strip()
                if ip:
                    self._located[inverter[CONF_DEVICE_ID]] = ip
            missing = [i for i in missing if i[CONF_DEVICE_ID] not in self._located]
        if not missing:
            return await self._async_create_entries()
        names = [labels[i[CONF_DEVICE_ID]] for i in missing]
        return self.async_show_form(
            step_id="addresses",
            data_schema=vol.Schema({vol.Optional(name): str for name in names}),
            errors={"base": "not_found"} if user_input is not None else {},
            description_placeholders={"names": ", ".join(names)},
        )

    async def _async_create_entries(self) -> ConfigFlowResult:
        """One entry per inverter: this flow creates the first, imports the rest."""
        entries = [
            {
                **inverter,
                CONF_DEVICE_IP: self._located[inverter[CONF_DEVICE_ID]],
                CONF_POLL_INTERVAL: inverter.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
            }
            for inverter in self._chosen
        ]
        first, *others = entries
        for data in others:
            self.hass.async_create_task(
                self.hass.config_entries.flow.async_init(
                    DOMAIN, context={"source": config_entries.SOURCE_IMPORT}, data=data
                )
            )
        await self.async_set_unique_id(first[CONF_DEVICE_ID])
        self._abort_if_unique_id_configured(updates=first)
        return self.async_create_entry(title=entry_title(first), data=first)

    async def async_step_import(self, data: dict) -> ConfigFlowResult:
        """An inverter picked in the same Smart Life login as another."""
        await self.async_set_unique_id(data[CONF_DEVICE_ID])
        self._abort_if_unique_id_configured(updates=data)
        return self.async_create_entry(title=entry_title(data), data=data)

    # --- Manual -------------------------------------------------------------

    async def async_step_manual(self, user_input=None) -> ConfigFlowResult:
        """Device ID and Local Key by hand; the IP is optional."""
        if user_input is None:
            return self.async_show_form(step_id="manual", data_schema=MANUAL_SCHEMA)
        data = {**user_input, CONF_DEVICE_ID: user_input[CONF_DEVICE_ID].strip()}
        await self.async_set_unique_id(data[CONF_DEVICE_ID])
        self._abort_if_unique_id_configured()
        # The connection is not tested: the device takes ~2 min to answer.
        if (data.get(CONF_DEVICE_IP) or "").strip():
            data[CONF_DEVICE_IP] = data[CONF_DEVICE_IP].strip()
            return self.async_create_entry(title=entry_title(data), data=data)
        data.pop(CONF_DEVICE_IP, None)
        self._chosen = [data]
        return await self.async_step_locate()


class SossenOptionsFlow(config_entries.OptionsFlow):
    """Overvoltage protection settings."""

    def __init__(self, config_entry) -> None:
        # Not self.config_entry: HA >= 2024.11 sets that itself.
        self._entry = config_entry

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = protection_errors(user_input)
            if not errors:
                return self.async_create_entry(
                    data={**self._entry.options, **user_input}
                )
        current = {**protection_settings(self._entry.options), **(user_input or {})}

        def volts(key):
            return vol.Required(key, default=float(current[key]))

        def watts(key):
            return vol.Required(key, default=int(current[key]))

        number = selector.NumberSelector
        cfg = selector.NumberSelectorConfig
        box = selector.NumberSelectorMode.BOX
        schema = vol.Schema(
            {
                vol.Required(CONF_PROTECTION, default=bool(current[CONF_PROTECTION])): bool,
                volts(CONF_HIGH_VOLTAGE): number(
                    cfg(min=230, max=260, step=0.5, unit_of_measurement="V", mode=box)
                ),
                volts(CONF_LOW_VOLTAGE): number(
                    cfg(min=220, max=255, step=0.5, unit_of_measurement="V", mode=box)
                ),
                watts(CONF_STEP_DOWN): number(
                    cfg(min=10, max=500, step=POWER_LIMIT_STEP, unit_of_measurement="W", mode=box)
                ),
                watts(CONF_STEP_UP): number(
                    cfg(min=10, max=500, step=POWER_LIMIT_STEP, unit_of_measurement="W", mode=box)
                ),
                watts(CONF_MIN_LIMIT): number(
                    cfg(min=0, max=2400, step=POWER_LIMIT_STEP, unit_of_measurement="W", mode=box)
                ),
                watts(CONF_INTERVAL): number(
                    cfg(min=60, max=1800, step=30, unit_of_measurement="s", mode=box)
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
