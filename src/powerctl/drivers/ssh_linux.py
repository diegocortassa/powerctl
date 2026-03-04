"""SSH driver for Linux hosts.

Uses ``asyncssh`` (optional dependency) when available, and falls back to
the stdlib ``subprocess`` + ``ssh`` CLI when it is not installed.

Install the optional dependency for better async support::

    pip install powerctl[ssh]
"""

from __future__ import annotations

import asyncio
import shlex
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

_DEFAULT_PORT = 22

# Graceful-reboot command tried in order until one is found on the target.
_REBOOT_CMDS = ["systemctl reboot", "reboot", "shutdown -r now"]
_SHUTDOWN_CMDS = ["systemctl poweroff", "poweroff", "shutdown -h now"]


@register_driver
class SSHLinuxDriver(BaseDriver):
    """Control Linux machines via SSH shell commands.

    Extra options (``Host.extra`` dict):

    ``sudo`` (bool, default ``True``)
        Prepend ``sudo`` to all power commands.
    ``sudo_password`` (str, optional)
        Password to feed to ``sudo -S`` when password-less sudo is not
        configured.
    """

    protocol = "ssh_linux"

    def __init__(self, host: Host) -> None:
        super().__init__(host)
        self._sudo: bool = host.extra.get("sudo", True)
        self._sudo_password: str | None = host.extra.get("sudo_password")
        self._ssh_client: Any = None  # asyncssh connection, if available
        self._insecure: bool = host.extra.get("insecure", False)

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Attempt to open a persistent asyncssh connection (best-effort)."""
        try:
            import asyncssh
        except ImportError:
            return  # Will use CLI fallback

        creds = self._host.credentials
        connect_kwargs: dict[str, Any] = {
            "host": self._host.hostname,
            "port": self._host.port or _DEFAULT_PORT,
            "username": creds.username,
            "known_hosts": None,  # disable host-key checking in lab envs
            "connect_timeout": self._host.timeout,
        }
        if creds.password:
            connect_kwargs["password"] = creds.password
        if creds.private_key_path:
            connect_kwargs["client_keys"] = [creds.private_key_path]
            if creds.private_key_passphrase:
                connect_kwargs["passphrase"] = creds.private_key_passphrase

        try:
            self._ssh_client = await asyncssh.connect(**connect_kwargs)
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
    # Internal command runner
    # ------------------------------------------------------------------

    def _wrap_sudo(self, cmd: str) -> str:
        if not self._sudo:
            return cmd
        if self._sudo_password:
            if not self._insecure:
                raise CommandError(
                    "Using sudo_password without asyncssh is not recommended, "
                    "install asyncssh python package or use "
                    'extra={"insecure": True} to force. The password will '
                    "be piped on the command line and briefly visible with "
                    "``ps`` on the remote host"
                )
            return f"echo {shlex.quote(self._sudo_password)} | sudo -S {cmd}"
        return f"sudo {cmd}"

    async def _run(self, command: str) -> tuple[int, str, str]:
        """Run *command* on the remote host; returns (returncode, stdout, stderr)."""
        if self._ssh_client is not None:
            # Prefer asyncssh's native handling
            if self._sudo and self._sudo_password:
                result = await self._ssh_client.run(
                    f"sudo -S {command}",
                    input=self._sudo_password,
                    check=False,
                )
                return result.exit_status, result.stdout, result.stderr
            else:
                # No sudo or no password needed
                wrapped = self._wrap_sudo(command)
                result = await self._ssh_client.run(wrapped, check=False)
                return result.exit_status, result.stdout, result.stderr

        # CLI fallback via subprocess
        wrapped = self._wrap_sudo(command)
        creds = self._host.credentials
        port = self._host.port or _DEFAULT_PORT
        ssh_cmd = [
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "BatchMode=yes" if not creds.password else "BatchMode=no",
            "-p",
            str(port),
        ]
        if creds.private_key_path:
            ssh_cmd += ["-i", creds.private_key_path]
        ssh_cmd += [f"{creds.username}@{self._host.hostname}", wrapped]

        proc = await asyncio.create_subprocess_exec(
            *ssh_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=self._host.timeout
        )
        return proc.returncode or 0, stdout_b.decode(), stderr_b.decode()

    async def _run_first_available(
        self, candidates: list[str], action: PowerAction
    ) -> PowerResult:
        """Try each command in *candidates* until one succeeds."""
        last_error: str = ""
        for cmd in candidates:
            rc, stdout, stderr = await self._run(cmd)
            if rc == 0:
                return PowerResult(
                    action,
                    success=True,
                    message=f"Command '{cmd}' succeeded.",
                    raw=stdout,
                )
            last_error = stderr or stdout
        raise CommandError(
            f"All commands failed for action '{action}'. Last error: {last_error}"
        )

    # ------------------------------------------------------------------
    # Power operations
    # ------------------------------------------------------------------

    async def power_on(self) -> PowerResult:
        raise UnsupportedOperationError(
            "power_on via SSH requires Wake-on-LAN or an OOB mechanism. "
            "The machine must already be reachable over SSH."
        )

    async def power_off(self) -> PowerResult:
        rc, _, stderr = await self._run("shutdown -h now || poweroff")
        if rc != 0:
            raise CommandError(f"power_off failed: {stderr}", exit_code=rc)
        return PowerResult(
            PowerAction.POWER_OFF,
            success=True,
            message="Shutdown command issued via SSH.",
        )

    async def power_cycle(self) -> PowerResult:
        raise UnsupportedOperationError(
            "Hard power-cycle via SSH is not supported. Use an OOB protocol"
            " like iDRAC or iLO."
        )

    async def reboot(self) -> PowerResult:
        return await self._run_first_available(_REBOOT_CMDS, PowerAction.REBOOT)

    async def shutdown(self) -> PowerResult:
        return await self._run_first_available(_SHUTDOWN_CMDS, PowerAction.SHUTDOWN)

    async def status(self) -> StatusResult:
        """Determine power status by probing the SSH port and running ``uptime``.

        * If the TCP connection is refused or times out ``OFF``
        * If connected and ``uptime`` succeeds ``ON``
        * Any other SSH error ``UNKNOWN``
        """
        import socket

        host = self._host.hostname
        port = self._host.port or _DEFAULT_PORT

        # Fast TCP probe — avoids a full SSH handshake when the host is off
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

        # Port is open — confirm with a lightweight command
        try:
            rc, stdout, _ = await self._run("uptime")
            if rc == 0:
                return StatusResult(
                    status=PowerStatus.ON,
                    message=f"Host is up: {stdout.strip()}",
                    raw=stdout,
                )
            # If uptime fails, it might be a permission issue, but the host is on.
            return StatusResult(
                PowerStatus.ON,
                message="Host is ON but 'uptime' command failed.",
                raw=rc,
            )
        except (AuthenticationError, ConnectionError):
            raise  # Propagate critical errors
        except Exception as exc:
            return StatusResult(PowerStatus.UNKNOWN, message=str(exc), raw=None)
