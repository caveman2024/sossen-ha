"""Smart Life QR login: read the inverters' Device IDs and Local Keys.

Used once, by the setup wizard. Nothing is stored except what a manual
setup would store (Device ID, Local Key, model, name): no account token is
kept, and all data flows over the LAN afterwards.

QR login approach adapted from ritonbrunis-lab/sossen-direct (MIT).
All functions here are blocking: run them in the executor.
"""

from __future__ import annotations

import logging
from typing import Any

from .const import (
    CONF_DEVICE_ID,
    CONF_LOCAL_KEY,
    CONF_MODEL,
    CONF_NAME,
    TUYA_CLIENT_ID,
    TUYA_SCHEMA,
    model_from_product,
)

_LOGGER = logging.getLogger(__name__)

TOKEN_KEYS = ("t", "uid", "expire_time", "access_token", "refresh_token")


def is_sossen(device: Any) -> bool:
    """Return True if a Tuya device looks like a SOSSEN microinverter."""
    text = f"{getattr(device, 'name', '')} {getattr(device, 'product_name', '')}"
    return "sossen" in text.lower()


def device_entry(device: Any) -> dict:
    """Turn a Tuya CustomerDevice into the fields of a config entry."""
    return {
        CONF_DEVICE_ID: device.id,
        CONF_NAME: device.name,
        CONF_LOCAL_KEY: device.local_key,
        CONF_MODEL: model_from_product(getattr(device, "product_name", "")),
    }


class QrLogin:
    """QR-code login against the Smart Life / Home Power app."""

    def __init__(self) -> None:
        # Imported here so the integration loads (and its tests run)
        # without the SDK; Home Assistant installs it from the manifest.
        from tuya_sharing import LoginControl

        self._control = LoginControl()
        self.user_code: str | None = None
        self.qr_code: str | None = None

    def request_qr(self, user_code: str) -> tuple[bool, dict]:
        """Ask Tuya for a QR code bound to this user code."""
        response = self._control.qr_code(TUYA_CLIENT_ID, TUYA_SCHEMA, user_code)
        if response.get("success"):
            self.user_code = user_code
            self.qr_code = response["result"]["qrcode"]
            return True, response
        return False, response

    def fetch_inverters(self) -> tuple[bool, list[dict] | dict]:
        """Once the QR code is scanned, list the account's SOSSEN inverters.

        Returns (False, error info) while the code has not been scanned.
        """
        from tuya_sharing import Manager, SharingTokenListener

        ok, info = self._control.login_result(
            self.qr_code, TUYA_CLIENT_ID, self.user_code
        )
        if not ok:
            return False, info

        class _Ignore(SharingTokenListener):
            def update_token(self, token_info: dict[str, Any]) -> None:
                pass  # nothing is kept: the session is used once

        manager = Manager(
            TUYA_CLIENT_ID,
            self.user_code,
            info["terminal_id"],
            info["endpoint"],
            {k: info[k] for k in TOKEN_KEYS},
            _Ignore(),
        )
        manager.update_device_cache()
        inverters = [
            device_entry(dev) for dev in manager.device_map.values() if is_sossen(dev)
        ]
        _LOGGER.debug(
            "Smart Life account has %d devices, %d SOSSEN inverters",
            len(manager.device_map), len(inverters),
        )
        return True, inverters
