"""Host and credential configuration dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Credentials:
    """Authentication credentials for a remote host."""

    username: str
    password: str | None = None
    private_key_path: str | None = None
    private_key_passphrase: str | None = None

    def __post_init__(self) -> None:
        if self.password is None and self.private_key_path is None:
            raise ValueError(
                "Either 'password' or 'private_key_path' must be provided."
            )


@dataclass(frozen=True)
class Host:
    """Represents a target machine and how to reach it.

    Args:
        hostname:    IP address or DNS name of the target.
        protocol:   Driver identifier (``"amt"``, ``"idrac"``, ``"ilo"``,
                                       ``"ssh_linux"``, ``"ssh_windows"``).
        credentials: Credentials() Authentication details.
        port:       Override the default port for the chosen protocol.
        timeout:    Per-operation timeout in seconds.
        extra:      Protocol-specific options forwarded verbatim to the driver.

    Example::

        host = Host(
            hostname="192.168.1.10",
            protocol="idrac",
            credentials=Credentials(username="root", password="calvin"),
        )
    """

    hostname: str
    protocol: str
    credentials: Credentials
    port: int | None = None
    timeout: float = 30.0
    extra: dict[str, Any] = field(default_factory=dict)
    """Protocol-specific options forwarded verbatim to the driver.

    **amt** (Intel AMT via WS-Management):
        - ``tls`` (bool, default ``True``): Use HTTPS instead of HTTP.
        - ``auth_method`` (str, default ``"digest"``): Authentication method
          (``"digest"`` or ``"basic"``).
        - ``verify_ssl`` (bool, default ``True``): Verify SSL certificates.

    **idrac** (Dell iDRAC via Redfish):
        - ``verify_ssl`` (bool, default ``True``): Verify SSL certificates.

    **ilo** (HP iLO via Redfish):
        - ``verify_ssl`` (bool, default ``True``): Verify SSL certificates.

    **ssh_linux** (Linux via SSH):
        - ``sudo`` (bool, default ``True``): Prepend ``sudo`` to all commands.
        - ``sudo_password`` (str, optional): Password for ``sudo -S`` if
          password-less sudo is not configured.
        - ``insecure`` (bool, default ``False``): Force passing sudo password
          insecurely when not using asyncssh

    **ssh_windows** (Windows via SSH or WinRM):
        - ``transport`` (str, default ``"ssh"``): Transport protocol
          (``"ssh"`` or ``"winrm"``).
        - ``https`` (bool, default ``False``): Use HTTPS for WinRM
          (default HTTP on port 5985, HTTPS on 5986).
        - ``winrm_transport`` (str, default ``"ntlm"`` or ``"kerberos"``):
          WinRM transport authentication (``"ntlm"``, ``"kerberos"``, etc.).
        - ``winrm_cert_validation`` (str, default ``"validate"``):
          WinRM SSL certificate validation (``"validate"``, ``"ignore"``, etc.).

    Example::

        # Disable SSL verification for iDRAC
        host = Host(
            hostname="192.168.1.10",
            protocol="idrac",
            credentials=Credentials(username="root", password="calvin"),
            extra={"verify_ssl": False}
        )

        # Use kerberos auth for WinRM
        host.extra["transport"] = "winrm"
        host.extra["https"] = True
        host.extra["winrm_transport"] = "kerberos"
    """
