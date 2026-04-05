"""powerctl - Remote power management for workstations and servers.

Supported protocols out of the box:

* ``idrac``       - Dell iDRAC (Redfish)
* ``ilo``         - HP iLO (Redfish)
* ``amt``         - Intel AMT (WS-Management)
* ``ssh_linux``   - SSH -> Linux shell
* ``ssh_windows`` - SSH / WinRM -> Windows PowerShell

Quick start::

    import asyncio
    from powerctl import PowerClient
    from powerctl.core import Host, Credentials

    host = Host(
        hostname="192.168.1.10",
        protocol="idrac",
        credentials=Credentials(username="root", password="calvin"),
    )

    async def main():
        async with PowerClient(host) as client:
            result = await client.reboot()
            print(result)

    asyncio.run(main())
"""

from .client import PowerClient, reboot_all, run_action_all
from .core import (
    AuthenticationError,
    BaseDriver,
    CommandError,
    ConnectionError,
    Credentials,
    DriverNotFoundError,
    Host,
    PowerAction,
    PowerCtlError,
    PowerResult,
    PowerStatus,
    StatusResult,
    TimeoutError,
    UnsupportedOperationError,
    list_protocols,
    register_driver,
)

__version_info__ = (0, 1, 2)
__version__ = ".".join(map(str, __version_info__))

__all__ = [
    # Main client
    "PowerClient",
    "reboot_all",
    "run_action_all",
    # Core types
    "Host",
    "Credentials",
    "PowerAction",
    "PowerResult",
    "PowerStatus",
    "StatusResult",
    "BaseDriver",
    # Registration
    "register_driver",
    "list_protocols",
    # Exceptions
    "PowerCtlError",
    "ConnectionError",
    "AuthenticationError",
    "UnsupportedOperationError",
    "DriverNotFoundError",
    "CommandError",
    "TimeoutError",
]
