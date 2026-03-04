"""Core abstractions and utilities."""

from .base import BaseDriver, PowerAction, PowerResult, PowerStatus, StatusResult
from .exceptions import (
    AuthenticationError,
    CommandError,
    ConnectionError,
    DriverNotFoundError,
    PowerCtlError,
    TimeoutError,
    UnsupportedOperationError,
)
from .host import Credentials, Host
from .registry import build_driver, get_driver_class, list_protocols, register_driver

__all__ = [
    # Base
    "BaseDriver",
    "PowerAction",
    "PowerResult",
    "PowerStatus",
    "StatusResult",
    # Config
    "Credentials",
    "Host",
    # Registry
    "register_driver",
    "build_driver",
    "get_driver_class",
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
