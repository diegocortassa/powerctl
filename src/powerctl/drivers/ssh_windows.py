"""Windows remote administration driver.

Supports two transports selectable via ``Host.extra["transport"]``:

``"ssh"`` (default)
    Requires OpenSSH Server to be installed and running on the target.
    Uses ``asyncssh`` if available, otherwise falls back to the ``ssh`` CLI.

``"winrm"``
    Uses the WinRM SOAP API (HTTP on port 5985, HTTPS on 5986).
    Requires ``pywinrm`` to be installed: ``pip install powerctl[winrm]``.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any

from ..core.base import BaseDriver, PowerAction, PowerResult, PowerStatus, StatusResult
from ..core.exceptions import (
    AuthenticationError,
    CommandError,
    ConnectionError,
    UnsupportedOperationError,
)
from ..core.host import Host
from ..core.registry import register_driver

_DEFAULT_SSH_PORT = 22
_DEFAULT_WINRM_HTTP_PORT = 5985
_DEFAULT_WINRM_HTTPS_PORT = 5986

# PowerShell commands for each action
_PS_REBOOT = "Restart-Computer -Force"
_PS_SHUTDOWN = "Stop-Computer -Force"


@register_driver
class SSHWindowsDriver(BaseDriver):
    """Control Windows machines via SSH or WinRM."""

    protocol = "ssh_windows"

    def __init__(self, host: Host) -> None:
        super().__init__(host)
        self._transport: str = host.extra.get("transport", "ssh")
        self._use_https: bool = host.extra.get("https", False)
        self._ssh_client: Any = None

    def _encode_ps_command(self, ps_command: str) -> str:
        """Return a Base64-encoded UTF-16LE PowerShell command suitable for
        passing to `powershell -EncodedCommand`.
        """
        return base64.b64encode(ps_command.encode("utf-16-le")).decode()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        if self._transport != "ssh":
            return
        try:
            import asyncssh
        except ImportError:
            return

        creds = self._host.credentials
        kwargs: dict[str, Any] = {
            "host": self._host.hostname,
            "port": self._host.port or _DEFAULT_SSH_PORT,
            "username": creds.username,
            "known_hosts": None,
            "connect_timeout": self._host.timeout,
        }
        if creds.password:
            kwargs["password"] = creds.password
        if creds.private_key_path:
            kwargs["client_keys"] = [creds.private_key_path]

        try:
            self._ssh_client = await asyncssh.connect(**kwargs)
        except asyncssh.PermissionDenied as exc:
            raise AuthenticationError(
                f"SSH authentication failed for {self._host.hostname}"
            ) from exc
        except (asyncssh.Error, OSError) as exc:
            raise ConnectionError(
                f"SSH connection to {self._host.hostname} failed: {exc}"
            ) from exc

    async def disconnect(self) -> None:
        if self._ssh_client is not None:
            self._ssh_client.close()
            self._ssh_client = None

    # ------------------------------------------------------------------
    # SSH transport
    # ------------------------------------------------------------------

    async def _run_ssh(self, command: str) -> tuple[int, str, str]:
        encoded = self._encode_ps_command(command)
        wrapped = f"powershell -NonInteractive -EncodedCommand {encoded}"

        if self._ssh_client is not None:
            result = await self._ssh_client.run(wrapped, check=False)
            return result.exit_status, result.stdout, result.stderr

        # CLI fallback
        creds = self._host.credentials
        port = self._host.port or _DEFAULT_SSH_PORT
        ssh_args = [
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-p",
            str(port),
        ]
        if creds.private_key_path:
            ssh_args += ["-i", creds.private_key_path]
        ssh_args += [f"{creds.username}@{self._host.hostname}", wrapped]

        proc = await asyncio.create_subprocess_exec(
            *ssh_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=self._host.timeout
        )
        return proc.returncode or 0, stdout_b.decode(), stderr_b.decode()

    # ------------------------------------------------------------------
    # WinRM transport
    # ------------------------------------------------------------------

    def _run_winrm(self, command: str) -> tuple[int, str, str]:
        """Run a command via WinRM. Must be run in a thread pool."""
        try:
            import winrm

            # pywinrm uses requests, so we need to handle its exceptions too.
            from requests.exceptions import RequestException
            from winrm.exceptions import (
                AuthenticationError,
                WinRMError,
            )
        except ImportError as exc:
            raise RuntimeError(
                "pywinrm or its dependencies are not installed. "
                "Run: pip install powerctl[winrm]"
            ) from exc

        creds = self._host.credentials
        scheme = "https" if self._use_https else "http"
        port = self._host.port or (
            _DEFAULT_WINRM_HTTPS_PORT if self._use_https else _DEFAULT_WINRM_HTTP_PORT
        )
        endpoint = f"{scheme}://{self._host.hostname}:{port}/wsman"

        session = winrm.Session(
            endpoint,
            auth=(creds.username, creds.password or ""),
            # NTLM is the default for HTTP, Kerberos for HTTPS.
            # Let pywinrm handle it unless specified.
            transport=self._host.extra.get(
                "winrm_transport", "ntlm" if not self._use_https else "kerberos"
            ),
            server_cert_validation=self._host.extra.get(
                "winrm_cert_validation", "validate"
            ),
            read_timeout_sec=self._host.timeout,
            connect_timeout_sec=self._host.timeout,
        )

        try:
            result = session.run_ps(command)
            rc = result.status_code
            return (
                rc,
                result.std_out.decode("utf-8", "ignore"),
                result.std_err.decode("utf-8", "ignore"),
            )
        except AuthenticationError as exc:
            raise AuthenticationError(
                f"WinRM authentication failed for {self._host.hostname}"
            ) from exc
        except (WinRMError, RequestException) as exc:
            # This covers a wide range of network issues: DNS failure, connection
            # refused, read timeouts, etc.
            raise ConnectionError(
                f"WinRM connection to {self._host.hostname} failed: {exc}"
            ) from exc
        except Exception as exc:
            # Catch any other unexpected errors from winrm library
            raise CommandError(f"WinRM command failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Unified run()
    # ------------------------------------------------------------------

    async def _run(self, ps_command: str) -> tuple[int, str, str]:
        if self._transport == "winrm":
            # winrm is blocking, run in executor
            return await asyncio.get_event_loop().run_in_executor(
                None, self._run_winrm, ps_command
            )
        # asyncssh is async-native
        return await self._run_ssh(ps_command)

    # ------------------------------------------------------------------
    # Power operations
    # ------------------------------------------------------------------

    async def power_on(self) -> PowerResult:
        raise UnsupportedOperationError(
            "power_on via Windows remote administration is not supported; "
            "the machine must already be running. Use Wake-on-LAN or an OOB protocol."
        )

    async def power_off(self) -> PowerResult:
        # Stop-Computer immediately cuts power after OS shutdown
        rc, _, stderr = await self._run("Stop-Computer -Force")
        if rc != 0:
            raise CommandError(f"Stop-Computer failed: {stderr}", exit_code=rc)
        return PowerResult(
            PowerAction.POWER_OFF, success=True, message="Stop-Computer issued."
        )

    async def power_cycle(self) -> PowerResult:
        raise UnsupportedOperationError(
            "Hard power-cycle via Windows remote administration is not supported. "
            "Use an OOB protocol (iDRAC, iLO, AMT)."
        )

    async def reboot(self) -> PowerResult:
        rc, _, stderr = await self._run(_PS_REBOOT)
        if rc != 0:
            raise CommandError(f"Restart-Computer failed: {stderr}", exit_code=rc)
        return PowerResult(
            PowerAction.REBOOT, success=True, message="Restart-Computer issued."
        )

    async def shutdown(self) -> PowerResult:
        rc, _, stderr = await self._run(_PS_SHUTDOWN)
        if rc != 0:
            raise CommandError(f"Stop-Computer failed: {stderr}", exit_code=rc)
        return PowerResult(
            PowerAction.SHUTDOWN, success=True, message="Stop-Computer issued."
        )

    async def status(self) -> StatusResult:
        """Determine power status by probing the transport port and running a command.

        * TCP connection refused / timeout -> ``OFF``
        * Connected and command succeeds   -> ``ON``
        * Any other error                  -> ``UNKNOWN``
        """
        import socket

        host = self._host.hostname
        if self._transport == "winrm":
            port = self._host.port or (
                _DEFAULT_WINRM_HTTPS_PORT
                if self._use_https
                else _DEFAULT_WINRM_HTTP_PORT
            )
        else:
            port = self._host.port or _DEFAULT_SSH_PORT

        try:
            loop = asyncio.get_event_loop()
            await asyncio.wait_for(
                loop.run_in_executor(
                    None, lambda: socket.create_connection((host, port), timeout=5)
                ),
                timeout=6,
            )
        except (TimeoutError, OSError):
            return StatusResult(
                status=PowerStatus.OFF,
                message=f"TCP port {port} unreachable on {host}.",
                raw=None,
            )

        try:
            rc, stdout, _ = await self._run("(Get-Date).ToString()")
            if rc == 0:
                return StatusResult(
                    status=PowerStatus.ON,
                    message=f"Host is up. Server time: {stdout.strip()}",
                    raw=stdout,
                )
            # If the command fails, the host is still on, but something is wrong.
            return StatusResult(
                PowerStatus.ON,
                message="Host is ON but remote command failed.",
                raw=rc,
            )
        except (AuthenticationError, ConnectionError):
            raise  # Propagate critical errors
        except Exception as exc:
            # For other unexpected errors, we report UNKNOWN status.
            return StatusResult(PowerStatus.UNKNOWN, message=str(exc), raw=None)
