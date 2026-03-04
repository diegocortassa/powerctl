"""Intel AMT driver via WS-Management (WS-Man) SOAP over HTTP(S).

Intel AMT exposes a WS-Management endpoint on port 16992 (HTTP) or 16993 (HTTPS).
This driver uses raw HTTP SOAP requests so it has no external dependencies.

Requires Intel AMT to be provisioned with a management account.
"""

from __future__ import annotations

import asyncio
import ssl
import textwrap
import xml.etree.ElementTree as ET
from base64 import b64encode
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPDigestAuthHandler,
    HTTPPasswordMgrWithDefaultRealm,
    HTTPSHandler,
    Request,
    build_opener,
)

from ..core.base import BaseDriver, PowerAction, PowerResult, PowerStatus, StatusResult
from ..core.exceptions import AuthenticationError, CommandError, ConnectionError
from ..core.host import Host
from ..core.registry import register_driver

# AMT power-state values (CIM_PowerManagementService RequestPowerStateChange)
_AMT_POWER_STATES: dict[str, int] = {
    "power_on": 2,  # Power On
    "power_off": 8,  # Power Off Hard
    "power_cycle": 5,  # Power Cycle (Off-Soft)
    "reboot": 10,  # Reset
    "shutdown": 12,  # Power Off Soft (OS graceful)
}

# CIM_AssociatedPowerManagementService.PowerState integer -> PowerStatus
# https://www.dmtf.org/sites/default/files/standards/documents/DSP0236_1.2.0.pdf
_AMT_STATUS_MAP: dict[int, PowerStatus] = {
    1: PowerStatus.UNKNOWN,  # Other
    2: PowerStatus.ON,  # Power On
    3: PowerStatus.POWERING_OFF,  # Sleep - Light (ACPI S1)
    4: PowerStatus.POWERING_OFF,  # Sleep - Deep (ACPI S3)
    5: PowerStatus.POWERING_OFF,  # Power Cycle (Off - Soft)
    6: PowerStatus.OFF,  # Power Off - Hard
    7: PowerStatus.REBOOTING,  # Hibernate (ACPI S4)
    8: PowerStatus.OFF,  # Power Off - Soft
    9: PowerStatus.POWERING_ON,  # Power Cycle (Off - Hard)
    10: PowerStatus.REBOOTING,  # Master Bus Reset
    11: PowerStatus.UNKNOWN,  # Diagnostic Interrupt (NMI)
    12: PowerStatus.OFF,  # Power Off - Soft Graceful
    13: PowerStatus.OFF,  # Power Off - Hard Graceful
    14: PowerStatus.REBOOTING,  # Master Bus Reset Graceful
    15: PowerStatus.POWERING_ON,  # Power Cycle (Off - Soft Graceful)
    16: PowerStatus.POWERING_ON,  # Power Cycle (Off - Hard Graceful)
}

_SOAP_ACTION_TEMPLATE = textwrap.dedent(
    """\
    <?xml version="1.0" encoding="UTF-8"?>
    <s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
                xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing"
                xmlns:wsman="http://schemas.dmtf.org/wbem/wsman/1/wsman.xsd"
                xmlns:p="http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_PowerManagementService">
      <s:Header>
        <wsa:Action>http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_PowerManagementService/RequestPowerStateChange</wsa:Action>
        <wsa:To>{endpoint}</wsa:To>
        <wsman:ResourceURI>http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_PowerManagementService</wsman:ResourceURI>
        <wsa:MessageID>uuid:powerctl-{action}</wsa:MessageID>
        <wsa:ReplyTo>
          <wsa:Address>http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous</wsa:Address>
        </wsa:ReplyTo>
      </s:Header>
      <s:Body>
        <p:RequestPowerStateChange_INPUT>
          <p:PowerState>{power_state}</p:PowerState>
          <p:ManagedElement>
            <wsa:Address>http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous</wsa:Address>
            <wsa:ReferenceParameters>
              <wsman:ResourceURI>http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_ComputerSystem</wsman:ResourceURI>
              <wsman:SelectorSet>
                <wsman:Selector Name="CreationClassName">CIM_ComputerSystem</wsman:Selector>
                <wsman:Selector Name="Name">ManagedSystem</wsman:Selector>
              </wsman:SelectorSet>
            </wsa:ReferenceParameters>
          </p:ManagedElement>
        </p:RequestPowerStateChange_INPUT>
      </s:Body>
    </s:Envelope>
"""  # noqa: E501
)

_SOAP_STATUS_TEMPLATE = textwrap.dedent(
    """\
    <?xml version="1.0" encoding="UTF-8"?>
    <s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
                xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing"
                xmlns:wsman="http://schemas.dmtf.org/wbem/wsman/1/wsman.xsd">
      <s:Header>
        <wsa:Action>http://schemas.xmlsoap.org/ws/2004/09/transfer/Get</wsa:Action>
        <wsa:To>{endpoint}</wsa:To>
        <wsman:ResourceURI>http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_AssociatedPowerManagementService</wsman:ResourceURI>
        <wsa:MessageID>uuid:powerctl-status</wsa:MessageID>
        <wsa:ReplyTo>
          <wsa:Address>http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous</wsa:Address>
        </wsa:ReplyTo>
      </s:Header>
      <s:Body/>
    </s:Envelope>
"""
)


@register_driver
class AMTDriver(BaseDriver):
    """Control workstations/servers via Intel Active Management Technology."""

    protocol = "amt"

    _DEFAULT_HTTP_PORT = 16992
    _DEFAULT_HTTPS_PORT = 16993

    def __init__(self, host: Host) -> None:
        super().__init__(host)
        self._use_tls: bool = host.extra.get("tls", True)
        self._auth_method: str = host.extra.get("auth_method", "digest")

    # ------------------------------------------------------------------
    # Pure helpers (no I/O - safe to call from any context))
    # ------------------------------------------------------------------

    def _endpoint_url(self) -> str:
        if self._use_tls:
            port = self._host.port or self._DEFAULT_HTTPS_PORT
            scheme = "https"
        else:
            port = self._host.port or self._DEFAULT_HTTP_PORT
            scheme = "http"
        return f"{scheme}://{self._host.hostname}:{port}/wsman"

    def _auth_header(self) -> str:
        creds = f"{self._host.credentials.username}:{self._host.credentials.password}"
        return "Basic " + b64encode(creds.encode()).decode()

    def _ssl_context(self) -> ssl.SSLContext | None:
        if not self._use_tls:
            return None
        ctx = ssl.create_default_context()
        if self._host.extra.get("verify_ssl", True):
            ctx.check_hostname = True
            ctx.verify_mode = ssl.CERT_REQUIRED
        else:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    # ------------------------------------------------------------------
    # Blocking I/O — NEVER call directly from async code
    # ------------------------------------------------------------------

    def _sync_send(self, soap_body: bytes) -> bytes:
        """Send a raw SOAP POST and return the response body.
        Must run in a thread pool."""
        url = self._endpoint_url()
        req = Request(
            url,
            data=soap_body,
            headers={
                "Content-Type": "application/soap+xml;charset=UTF-8",
            },
            method="POST",
        )

        opener_args: list[Any] = []
        if self._use_tls:
            opener_args.append(HTTPSHandler(context=self._ssl_context()))

        if self._auth_method == "digest":
            password_mgr = HTTPPasswordMgrWithDefaultRealm()
            password_mgr.add_password(
                None,
                url,
                self._host.credentials.username,
                self._host.credentials.password,  # type: ignore
            )
            opener_args.append(HTTPDigestAuthHandler(password_mgr))
        elif self._auth_method == "basic":
            req.add_header("Authorization", self._auth_header())
        else:
            raise ValueError(f"Unsupported auth_method: '{self._auth_method}'")

        opener = build_opener(*opener_args)

        try:
            with opener.open(req, timeout=self._host.timeout) as resp:
                return cast(bytes, resp.read())
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise AuthenticationError(
                    f"AMT authentication failed on {self._host.hostname}"
                ) from exc
            raise CommandError(
                f"AMT request failed: HTTP {exc.code}", exit_code=exc.code
            ) from exc
        except URLError as exc:
            raise ConnectionError(
                f"Cannot reach AMT on {self._host.hostname}: {exc.reason}"
            ) from exc

    def _sync_send_action(self, action: str) -> None:
        """Build and send the power-action SOAP envelope. Must run in a thread pool."""
        body = _SOAP_ACTION_TEMPLATE.format(
            endpoint=self._endpoint_url(),
            action=action,
            power_state=_AMT_POWER_STATES[action],
        ).encode("utf-8")
        self._sync_send(body)  # response body is not needed for actions

    def _sync_query_status(self) -> StatusResult:
        """Fetch the current power state via WS-Man GET. Must run in a thread pool."""
        body = _SOAP_STATUS_TEMPLATE.format(endpoint=self._endpoint_url()).encode(
            "utf-8"
        )
        response = self._sync_send(body)

        root = ET.fromstring(response)
        ns = (
            "http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/"
            "CIM_AssociatedPowerManagementService"
        )
        ps_el = root.find(f".//{{{ns}}}PowerState")
        if ps_el is None or not ps_el.text:
            return StatusResult(
                PowerStatus.UNKNOWN,
                message="PowerState element not found in AMT response.",
                raw=response,
            )

        raw_int = int(ps_el.text)
        return StatusResult(
            status=_AMT_STATUS_MAP.get(raw_int, PowerStatus.UNKNOWN),
            message=f"AMT PowerState code: {raw_int}",
            raw=raw_int,
        )

    # ------------------------------------------------------------------
    # Power operations
    # ------------------------------------------------------------------

    async def power_on(self) -> PowerResult:
        await asyncio.to_thread(self._sync_send_action, "power_on")
        return PowerResult(
            PowerAction.POWER_ON, success=True, message="Power-on sent via Intel AMT."
        )

    async def power_off(self) -> PowerResult:
        await asyncio.to_thread(self._sync_send_action, "power_off")
        return PowerResult(
            PowerAction.POWER_OFF,
            success=True,
            message="Hard power-off sent via Intel AMT.",
        )

    async def power_cycle(self) -> PowerResult:
        await asyncio.to_thread(self._sync_send_action, "power_cycle")
        return PowerResult(
            PowerAction.POWER_CYCLE,
            success=True,
            message="Power-cycle sent via Intel AMT.",
        )

    async def reboot(self) -> PowerResult:
        await asyncio.to_thread(self._sync_send_action, "reboot")
        return PowerResult(
            PowerAction.REBOOT, success=True, message="Reset sent via Intel AMT."
        )

    async def shutdown(self) -> PowerResult:
        await asyncio.to_thread(self._sync_send_action, "shutdown")
        return PowerResult(
            PowerAction.SHUTDOWN,
            success=True,
            message="Graceful shutdown sent via Intel AMT.",
        )

    async def status(self) -> StatusResult:
        return await asyncio.to_thread(self._sync_query_status)
