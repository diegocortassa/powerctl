"""Dell iDRAC driver via Redfish REST API.

Tested against iDRAC 8 / 9 with Redfish v1.
Default port: 443.
"""

from __future__ import annotations

from ..core.base import BaseDriver, PowerAction, PowerResult, StatusResult
from ..core.host import Host
from ..core.registry import register_driver
from ._redfish import RedfishMixin


@register_driver
class IDRACDriver(RedfishMixin, BaseDriver):
    """Control Dell servers through iDRAC's Redfish interface."""

    protocol = "idrac"

    # Dell iDRAC Redfish paths
    _POWER_URI = "/redfish/v1/Systems/System.Embedded.1"
    _RESET_URI = "/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset"

    def __init__(self, host: Host) -> None:
        BaseDriver.__init__(self, host)

    # ------------------------------------------------------------------
    # Power operations
    # ------------------------------------------------------------------

    async def power_on(self) -> PowerResult:
        await self._reset("power_on")
        return PowerResult(
            PowerAction.POWER_ON,
            success=True,
            message="Power-on command sent via iDRAC Redfish.",
        )

    async def power_off(self) -> PowerResult:
        await self._reset("power_off")
        return PowerResult(
            PowerAction.POWER_OFF,
            success=True,
            message="Force-off command sent via iDRAC Redfish.",
        )

    async def power_cycle(self) -> PowerResult:
        await self._reset("power_cycle")
        return PowerResult(
            PowerAction.POWER_CYCLE,
            success=True,
            message="Power-cycle command sent via iDRAC Redfish.",
        )

    async def reboot(self) -> PowerResult:
        await self._reset("reboot")
        return PowerResult(
            PowerAction.REBOOT,
            success=True,
            message="Graceful-restart command sent via iDRAC Redfish.",
        )

    async def shutdown(self) -> PowerResult:
        await self._reset("shutdown")
        return PowerResult(
            PowerAction.SHUTDOWN,
            success=True,
            message="Graceful-shutdown command sent via iDRAC Redfish.",
        )

    async def status(self) -> StatusResult:
        return await self._query_status()
