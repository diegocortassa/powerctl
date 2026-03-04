"""High-level public API for powerctl.

:class:`PowerClient` is the main entry point most users will interact with.
It wraps driver instantiation, context management, and concurrent execution.

Quick example::

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

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import cast

# Ensure all built-in drivers are registered when the client is imported.
from . import drivers as _drivers  # noqa: F401
from .core.base import BaseDriver, PowerResult, StatusResult
from .core.host import Host
from .core.registry import build_driver


class PowerClient:
    """Facade that drives a single host using the appropriate protocol driver.

    Parameters
    ----------
    host:
        Fully configured :class:`~powerctl.core.host.Host` instance.

    Usage as an async context manager is recommended - it calls
    :meth:`~powerctl.core.base.BaseDriver.connect` and
    :meth:`~powerctl.core.base.BaseDriver.disconnect` automatically::

        async with PowerClient(host) as client:
            await client.reboot()

    You can also use it without the context manager, but then you are
    responsible for calling :meth:`connect` and :meth:`disconnect`.
    """

    def __init__(self, host: Host) -> None:
        self._host = host
        self._driver: BaseDriver = build_driver(host)

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> PowerClient:
        await self._driver.connect()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._driver.disconnect()

    # ------------------------------------------------------------------
    # Lifecycle (explicit, for use without context manager)
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Explicitly open the connection to the remote host."""
        await self._driver.connect()

    async def disconnect(self) -> None:
        """Explicitly close the connection to the remote host."""
        await self._driver.disconnect()

    # ------------------------------------------------------------------
    # Power operations
    # ------------------------------------------------------------------

    async def power_on(self) -> PowerResult:
        """Turn the machine on."""
        return await self._driver.power_on()

    async def power_off(self) -> PowerResult:
        """Cut power immediately (hard power-off)."""
        return await self._driver.power_off()

    async def power_cycle(self) -> PowerResult:
        """Hard power-cycle (power off then on)."""
        return await self._driver.power_cycle()

    async def reboot(self) -> PowerResult:
        """Graceful OS reboot."""
        return await self._driver.reboot()

    async def shutdown(self) -> PowerResult:
        """Graceful OS shutdown."""
        return await self._driver.shutdown()

    async def status(self) -> StatusResult:
        """Return the current power status of the machine."""
        return await self._driver.status()

    # ------------------------------------------------------------------
    # Repr
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"<PowerClient host={self._host.hostname} protocol={self._host.protocol}>"
        )


# ---------------------------------------------------------------------------
# Bulk helpers
# ---------------------------------------------------------------------------


async def reboot_all(
    hosts: Iterable[Host], *, max_concurrent: int = 10
) -> list[PowerResult]:
    """Reboot *hosts* concurrently.

    Parameters
    ----------
    hosts:
        Iterable of :class:`~powerctl.core.host.Host` objects.
    max_concurrent:
        Maximum number of simultaneous operations.

    Returns
    -------
    list[PowerResult]
        One result per host, in the same order as *hosts*.
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _reboot_one(host: Host) -> PowerResult:
        async with semaphore:
            async with PowerClient(host) as client:
                return await client.reboot()

    return await asyncio.gather(*[_reboot_one(h) for h in hosts])


async def run_action_all(
    hosts: Iterable[Host],
    action: str,
    *,
    max_concurrent: int = 10,
) -> list[PowerResult]:
    """Run an arbitrary *action* on all *hosts* concurrently.

    Parameters
    ----------
    action:
        One of ``"power_on"``, ``"power_off"``, ``"power_cycle"``,
        ``"reboot"``, ``"shutdown"`` or ``"status"``.
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _run_one(host: Host) -> PowerResult:
        async with semaphore:
            async with PowerClient(host) as client:
                method = getattr(client, action)
                return cast(PowerResult, await method())

    return cast(list[PowerResult], await asyncio.gather(*[_run_one(h) for h in hosts]))
