"""Driver registry - plug-in system.

How to register a new driver
------------------------------
Option 1 decorator (recommended):

    from powerctl.core.registry import register_driver
    from powerctl.core.base import BaseDriver

    @register_driver
    class MyDriver(BaseDriver):
        protocol = "my_protocol"
        ...

Option 2 explicit call:

    register_driver(MyDriver)

Both approaches make the driver available immediately to
:func:`get_driver` and :class:`~powerctl.client.PowerClient`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .exceptions import DriverNotFoundError

if TYPE_CHECKING:
    from .base import BaseDriver
    from .host import Host

# Internal registry: protocol name -> driver class
_REGISTRY: dict[str, type[BaseDriver]] = {}


def register_driver(cls: type[BaseDriver]) -> type[BaseDriver]:
    """Register *cls* as the driver for ``cls.protocol``.

    Can be used as a plain class decorator::

        @register_driver
        class MyDriver(BaseDriver):
            protocol = "my_protocol"

    Returns the class unchanged so it can still be subclassed.
    """
    if not hasattr(cls, "protocol") or not cls.protocol:
        raise TypeError(
            f"Driver {cls.__name__} must define a non-empty `protocol` class attribute."
        )
    if cls.protocol in _REGISTRY:
        raise ValueError(
            f"Protocol '{cls.protocol}' is already registered by "
            f"{_REGISTRY[cls.protocol].__name__}. "
            "Use a unique protocol name."
        )
    _REGISTRY[cls.protocol] = cls
    return cls


def unregister_driver(protocol: str) -> None:
    """Remove a driver from the registry (useful in tests)."""
    _REGISTRY.pop(protocol, None)


def get_driver_class(protocol: str) -> type[BaseDriver]:
    """Return the driver class for *protocol*, or raise :exc:`DriverNotFoundError`."""
    try:
        return _REGISTRY[protocol]
    except KeyError:
        available = ", ".join(sorted(_REGISTRY)) or "<none>"
        raise DriverNotFoundError(
            f"No driver registered for protocol '{protocol}'. "
            f"Available protocols: {available}"
        ) from None


def build_driver(host: Host) -> BaseDriver:
    """Instantiate and return the correct driver for *host*."""
    driver_cls = get_driver_class(host.protocol)
    return driver_cls(host)


def list_protocols() -> list[str]:
    """Return the sorted list of all registered protocol identifiers."""
    return sorted(_REGISTRY)
