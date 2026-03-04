"""Built-in protocol drivers.

Importing this package ensures all built-in drivers are registered.
"""

# Order does not matter; each module calls @register_driver on import.
from . import amt, idrac, ilo, ssh_linux, ssh_windows  # noqa: F401

__all__ = ["amt", "idrac", "ilo", "ssh_linux", "ssh_windows"]
