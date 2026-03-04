"""Abstract base class that every driver must implement."""

from __future__ import annotations

import abc
from enum import Enum

from .host import Host


class PowerAction(str, Enum):  # noqa: UP042 # will migrate to StrEnum later
    """Enumeration of supported power actions."""

    POWER_ON = "power_on"
    POWER_OFF = "power_off"
    POWER_CYCLE = "power_cycle"
    REBOOT = "reboot"
    SHUTDOWN = "shutdown"


class PowerStatus(str, Enum):  # noqa: UP042 # will migrate to StrEnum later
    """Possible power states reported by :meth:`BaseDriver.status`.

    ``UNKNOWN`` is returned when the driver can reach the BMC/host but
    cannot map the raw state to a well-known value (e.g. firmware update
    in progress, POST, transitioning).
    """

    ON = "on"  # Machine is powered on and running
    OFF = "off"  # Machine is powered off (soft or hard)
    REBOOTING = "rebooting"  # OS-level reboot in progress
    POWERING_ON = "powering_on"  # Power-on sequence in progress
    POWERING_OFF = "powering_off"  # Graceful shutdown in progress
    UNKNOWN = "unknown"  # State cannot be determined


class PowerResult:
    """Result returned by every driver power-action operation.

    Attributes:
        action:   The action that was executed.
        success:  Whether the command was accepted by the remote host.
        message:  Human-readable description of the outcome.
        raw:      Optional raw response from the driver (for debugging).
    """

    def __init__(
        self,
        action: PowerAction,
        success: bool,
        message: str = "",
        raw: object = None,
    ) -> None:
        self.action = action
        self.success = success
        self.message = message
        self.raw = raw

    def __repr__(self) -> str:
        status = "OK" if self.success else "FAILED"
        return (
            f"<PowerResult action={self.action} status={status} msg={self.message!r}>"
        )


class StatusResult:
    """Result returned by :meth:`BaseDriver.status`.

    Attributes:
        status:   The current :class:`PowerStatus` of the machine.
        message:  Human-readable description (includes raw state string when
                  the driver cannot map it to a known value).
        raw:      The unmodified state string returned by the remote API,
                  useful for debugging vendor-specific states.
    """

    def __init__(
        self,
        status: PowerStatus,
        message: str = "",
        raw: object = None,
    ) -> None:
        self.status = status
        self.message = message
        self.raw = raw

    @property
    def is_on(self) -> bool:
        """``True`` when the machine is fully powered on."""
        return self.status == PowerStatus.ON

    @property
    def is_off(self) -> bool:
        """``True`` when the machine is fully powered off."""
        return self.status == PowerStatus.OFF

    def __repr__(self) -> str:
        return f"<StatusResult status={self.status.value} msg={self.message!r}>"


class BaseDriver(abc.ABC):
    """Contract that every protocol driver must fulfil.

    Subclass this, implement every abstract method, then register the driver
    with :func:`~powerctl.core.registry.register_driver`.

    All methods are **async** so that multiple hosts can be driven concurrently
    without blocking threads.
    """

    #: Short identifier used to look up this driver (e.g. ``"idrac"``).
    #: Must be unique across all registered drivers.
    protocol: str

    def __init__(self, host: Host) -> None:
        self._host = host

    # ------------------------------------------------------------------
    # Lifecycle helpers (optional override)
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Establish / validate the connection to the remote host.

        Override when the protocol requires an explicit handshake (e.g. SSH).
        The default implementation is a no-op.
        """

    async def disconnect(self) -> None:
        """Tear down the connection.

        Override when cleanup is required. Default is a no-op.
        """

    # ------------------------------------------------------------------
    # Context-manager support (works out-of-the-box for all drivers)
    # ------------------------------------------------------------------

    async def __aenter__(self) -> BaseDriver:
        await self.connect()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.disconnect()

    # ------------------------------------------------------------------
    # Power operations — must be implemented by every driver
    # ------------------------------------------------------------------

    @abc.abstractmethod
    async def power_on(self) -> PowerResult:
        """Turn the machine on (or wake it from a powered-off state)."""

    @abc.abstractmethod
    async def power_off(self) -> PowerResult:
        """Cut power immediately (equivalent to pulling the plug)."""

    @abc.abstractmethod
    async def power_cycle(self) -> PowerResult:
        """Hard power-cycle (power off then on)."""

    @abc.abstractmethod
    async def reboot(self) -> PowerResult:
        """Graceful OS reboot (ACPI soft reboot)."""

    @abc.abstractmethod
    async def shutdown(self) -> PowerResult:
        """Graceful OS shutdown (halt/power-off via OS)."""

    @abc.abstractmethod
    async def status(self) -> StatusResult:
        """Return the current power status of the machine.

        Implementations should query the remote host in real time and map the
        vendor-specific state string to a :class:`PowerStatus` value.  When
        the raw state cannot be mapped, return ``PowerStatus.UNKNOWN`` and
        include the raw value in :attr:`StatusResult.raw` for diagnostics.
        """

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    @property
    def host(self) -> Host:
        return self._host

    def __repr__(self) -> str:
        return f"<{type(self).__name__} host={self._host.hostname}>"
