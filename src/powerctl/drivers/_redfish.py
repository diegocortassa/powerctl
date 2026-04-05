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
from urllib.parse import urljoin
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

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


# Preferred reset type, in order of preference, for each logical action.
# The first value that appears in the firmware's AllowableValues list wins.
_RESET_TYPE_FALLBACKS: dict[str, list[str]] = {
    "power_on": ["On"],
    "power_off": ["ForceOff", "Off"],
    "power_cycle": ["ForceRestart", "PowerCycle"],
    "reboot": ["GracefulRestart", "ForceRestart"],
    # iLO 4 / iLO 5 < 1.40 don't have GracefulShutdown — fall back to
    # PushPowerButton which triggers the OS shutdown via ACPI.
    "shutdown": ["GracefulShutdown", "PushPowerButton"],
}


class HTTPRedirectHandler308(HTTPRedirectHandler):
    """Custom redirect handler that properly follows HTTP 308 (Permanent Redirect).

    This is needed for HP iLO devices which may return 308 responses that should
    be followed even for POST requests.
    """

    def http_error_308(
        self, req: Request, fp: Any, code: int, msg: str, headers: Any
    ) -> Any:
        """Handle HTTP 308 Permanent Redirect."""
        if "location" in headers:
            newurl = urljoin(req.full_url, headers["location"])
            newheaders = {
                k: v
                for k, v in req.headers.items()
                if k.lower() not in ("content-length", "host")
            }
            new_req = Request(
                newurl,
                data=req.data,
                headers=newheaders,
                origin_req_host=req.origin_req_host,
                unverifiable=req.unverifiable,
                method=req.get_method(),
            )
            # Some Redfish servers (like iLO) are case-sensitive
            # and reject 'Content-type'
            if new_req.data is not None:
                new_req.headers.pop("Content-type", None)
                new_req.headers["Content-Type"] = "application/json"

            return self.parent.open(new_req, timeout=req.timeout)
        return None

    # Alias for historical reasons (some versions use https_error_308)
    https_error_308 = http_error_308


class RedfishMixin:
    """HTTP helpers for Redfish-based drivers (iDRAC, iLO).

    Requires ``self._host: Host`` to be set before calling any method.
    """

    # Subclasses MUST override these to point to the correct Redfish paths.
    _POWER_URI: str = "/redfish/v1/Systems/1"
    _RESET_URI: str = "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset"

    _host: Host

    # Cache of resolved action -> reset type string, populated on first use.
    # Stored per-instance so each host gets its own capability snapshot.
    _resolved_reset_types: dict[str, str] | None = None

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

    def _base_headers(self) -> dict[str, str]:
        """Build common headers that are always needed."""
        creds = f"{self._host.credentials.username}:{self._host.credentials.password}"
        token = base64.b64encode(creds.encode()).decode()
        return {
            "Authorization": f"Basic {token}",
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

        # Build headers, only including Content-Type when there's a body
        headers = self._base_headers()
        if data is not None:
            headers["Content-Type"] = "application/json"

        req = Request(url, data=data, headers=headers, method=method)
        # Some Redfish servers (like iLO) are case-sensitive
        # and reject 'Content-type'
        if data is not None:
            req.headers.pop("Content-type", None)
            req.headers["Content-Type"] = "application/json"

        # Create opener with custom 308 redirect handler and SSL context
        https_handler = HTTPSHandler(context=self._ssl_context())
        opener = build_opener(HTTPRedirectHandler308(), https_handler)

        try:
            with opener.open(req, timeout=self._host.timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise AuthenticationError(
                    f"Authentication failed for {self._host.hostname}: HTTP {exc.code}"
                ) from exc

            # Try to extract error detail from response body
            detail = ""
            try:
                raw = exc.read()
                if raw:
                    error_body = json.loads(raw)
                    # Redfish extended error format
                    messages = error_body.get("error", {}).get(
                        "@Message.ExtendedInfo", []
                    ) or error_body.get("Messages", [])
                    if messages:
                        ids = ", ".join(
                            m.get("MessageID", "")
                            for m in messages
                            if m.get("MessageID")
                        )
                        args = messages[0].get("MessageArgs", [])
                        detail = f" [{ids}]"
                        if args:
                            detail += f": {', '.join(str(a) for a in args)}"
            except Exception:
                pass

            raise CommandError(
                f"HTTP {exc.code} from {url}: {exc.reason}{detail}", exit_code=exc.code
            ) from exc
        except URLError as exc:
            raise ConnectionError(
                f"Cannot reach {self._host.hostname}: {exc.reason}"
            ) from exc

    def _sync_fetch_allowable_reset_types(self) -> set[str]:
        """Query the system resource and extract AllowableValues for Reset.

        Falls back to an empty set if the firmware doesn't advertise them,
        in which case the caller will use the first (preferred) value blindly.
        """
        try:
            data = self._sync_request("GET", self._POWER_URI)
        except (CommandError, ConnectionError):
            return set()

        # Standard Redfish location:
        # Actions -> #ComputerSystem.Reset -> ResetType@Redfish.AllowableValues
        actions = data.get("Actions", {})
        reset_action = actions.get("#ComputerSystem.Reset", {})
        allowable: list[str] = reset_action.get("ResetType@Redfish.AllowableValues", [])

        # Some older firmware puts them one level up or uses a different key
        if not allowable:
            allowable = reset_action.get("AllowableValues", [])

        return set(allowable)

    def _sync_build_reset_map(self) -> dict[str, str]:
        """Resolve each logical action to the best supported reset type."""
        allowable = self._sync_fetch_allowable_reset_types()
        resolved: dict[str, str] = {}

        for action, candidates in _RESET_TYPE_FALLBACKS.items():
            if allowable:
                # Pick the first candidate the firmware actually advertises
                chosen = next((c for c in candidates if c in allowable), None)
                if chosen is None:
                    # Nothing matched — use the first candidate and let the
                    # firmware reject it with a clear error message
                    chosen = candidates[0]
            else:
                # Firmware didn't advertise anything; trust our preference order
                chosen = candidates[0]
            resolved[action] = chosen

        return resolved

    def _sync_resolve_reset_type(self, action: str) -> str:
        """Return the resolved reset type for *action*, building the cache if needed."""
        if self._resolved_reset_types is None:
            self._resolved_reset_types = self._sync_build_reset_map()
        return self._resolved_reset_types[action]

    def _sync_reset(self, action: str) -> dict[str, Any]:
        """Send a Redfish reset action. Must run in a thread pool."""
        reset_type = self._sync_resolve_reset_type(action)
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

    async def _prefetch_capabilities(self) -> None:
        """Eagerly populate the reset-type cache. Optional but useful on connect."""
        await asyncio.to_thread(self._sync_build_reset_map)
