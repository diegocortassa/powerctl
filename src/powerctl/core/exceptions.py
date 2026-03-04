"""Custom exception hierarchy for powerctl."""


class PowerCtlError(Exception):
    """Base exception for all powerctl errors."""


class ConnectionError(PowerCtlError):
    """Raised when a connection to the target host cannot be established."""


class AuthenticationError(PowerCtlError):
    """Raised when credentials are rejected by the target host."""


class UnsupportedOperationError(PowerCtlError):
    """Raised when the driver does not support the requested operation."""


class DriverNotFoundError(PowerCtlError):
    """Raised when no driver is registered for the requested protocol."""


class CommandError(PowerCtlError):
    """Raised when a power command fails on the remote host."""

    def __init__(self, message: str, exit_code: int | None = None) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class TimeoutError(PowerCtlError):
    """Raised when an operation exceeds its allowed time."""
