"""Shared Redfish HTTP helpers used by iDRAC and iLO drivers.

Both Dell iDRAC and HP iLO expose a Redfish REST API.  This mixin
encapsulates the common HTTP machinery so each driver only has to
provide endpoint-specific details.

All public methods are **async** and offload blocking ``urllib`` I/O to a
thread pool via ``asyncio.to_thread``, keeping the event loop free during
network waits.  This is critical for ``reboot_all`` and other bulk helpers
that run many operations concurrently.
"""

from __future__ import annotations

import asyncio
import base64
import json
import ssl
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..core.base import PowerStatus, StatusResult
from ..core.exceptions import AuthenticationError, CommandError, ConnectionError
from ..core.host import Host

# Redfish PowerState string -> PowerStatus
# https://redfish.dmtf.org/schemas/v1/Resource.json  (PowerState enum)
_REDFISH_POWER_STATE_MAP: dict[str, PowerStatus] = {
    "On": PowerStatus.ON,
    "Off": PowerStatus.OFF,
    "PoweringOn": PowerStatus.POWERING_ON,
    "PoweringOff": PowerStatus.POWERING_OFF,
}


class RedfishMixin:
    """HTTP helpers for Redfish-based drivers (iDRAC, iLO).

    Requires ``self._host: Host`` to be set before calling any method.
    """

    # Subclasses MUST override these to point to the correct Redfish paths.
    _POWER_URI: str = "/redfish/v1/Systems/1"
    _RESET_URI: str = "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset"

    # Redfish reset-type values (Redfish standard names)
    _RESET_TYPE_MAP: dict[str, str] = {
        "power_on": "On",
        "power_off": "ForceOff",
        "power_cycle": "ForceRestart",
        "reboot": "GracefulRestart",
        "shutdown": "GracefulShutdown",
    }

    _host: Host  # provided by the concrete driver class

    # ------------------------------------------------------------------
    # Pure helpers (no I/O - safe to call from any context)
    # ------------------------------------------------------------------

    def _base_url(self) -> str:
        port = self._host.port or 443
        return f"https://{self._host.hostname}:{port}"

    def _ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        if self._host.extra.get("verify_ssl", True):
            ctx.check_hostname = True
            ctx.verify_mode = ssl.CERT_REQUIRED
        else:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _headers(self) -> dict[str, str]:
        creds = f"{self._host.credentials.username}:{self._host.credentials.password}"
        token = base64.b64encode(creds.encode()).decode()
        return {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ------------------------------------------------------------------
    # Blocking I/O - NEVER call directly from async code
    # ------------------------------------------------------------------

    def _sync_request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Execute a synchronous HTTP request. Must run in a thread pool."""
        url = self._base_url() + path
        data = json.dumps(body).encode() if body else None
        req = Request(url, data=data, headers=self._headers(), method=method)
        try:
            with urlopen(
                req, context=self._ssl_context(), timeout=self._host.timeout
            ) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise AuthenticationError(
                    f"Authentication failed for {self._host.hostname}: HTTP {exc.code}"
                ) from exc
            raise CommandError(
                f"HTTP {exc.code} from {url}: {exc.reason}", exit_code=exc.code
            ) from exc
        except URLError as exc:
            raise ConnectionError(
                f"Cannot reach {self._host.hostname}: {exc.reason}"
            ) from exc

    def _sync_reset(self, action: str) -> dict[str, Any]:
        """Send a Redfish reset action. Must run in a thread pool."""
        reset_type = self._RESET_TYPE_MAP[action]
        return self._sync_request("POST", self._RESET_URI, {"ResetType": reset_type})

    def _sync_query_status(self) -> StatusResult:
        """Fetch the current PowerState from the Redfish system resource.
        Must run in a thread pool."""
        data = self._sync_request("GET", self._POWER_URI)
        raw_state: str = data.get("PowerState", "")
        power_status = _REDFISH_POWER_STATE_MAP.get(raw_state, PowerStatus.UNKNOWN)
        return StatusResult(
            status=power_status,
            message=f"Redfish PowerState: '{raw_state}'",
            raw=raw_state,
        )

    # ------------------------------------------------------------------
    # Async wrappers - always use these from coroutines
    # ------------------------------------------------------------------

    async def _reset(self, action: str) -> dict[str, Any]:
        """Async wrapper: offloads the blocking Redfish POST to a thread."""
        return await asyncio.to_thread(self._sync_reset, action)

    async def _query_status(self) -> StatusResult:
        """Async wrapper: offloads the blocking Redfish GET to a thread."""
        return await asyncio.to_thread(self._sync_query_status)
