"""Tests for powerctl.

Run with:  pytest
"""

from __future__ import annotations

import pytest

from powerctl import (
    BaseDriver,
    Credentials,
    DriverNotFoundError,
    Host,
    PowerAction,
    PowerClient,
    PowerResult,
    PowerStatus,
    StatusResult,
    list_protocols,
    register_driver,
)
from powerctl.core.registry import unregister_driver

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def make_host(protocol: str = "idrac", **extra: object) -> Host:
    return Host(
        hostname="192.168.1.1",
        protocol=protocol,
        credentials=Credentials(username="admin", password="secret"),
        extra=dict(extra),
    )


class _DummyDriver(BaseDriver):
    """A no-op driver used in unit tests."""

    protocol = "_dummy"

    def __init__(self, host: Host) -> None:
        super().__init__(host)
        self.calls: list[str] = []

    async def power_on(self) -> PowerResult:
        self.calls.append("power_on")
        return PowerResult(PowerAction.POWER_ON, success=True)

    async def power_off(self) -> PowerResult:
        self.calls.append("power_off")
        return PowerResult(PowerAction.POWER_OFF, success=True)

    async def power_cycle(self) -> PowerResult:
        self.calls.append("power_cycle")
        return PowerResult(PowerAction.POWER_CYCLE, success=True)

    async def reboot(self) -> PowerResult:
        self.calls.append("reboot")
        return PowerResult(PowerAction.REBOOT, success=True)

    async def shutdown(self) -> PowerResult:
        self.calls.append("shutdown")
        return PowerResult(PowerAction.SHUTDOWN, success=True)

    async def status(self) -> StatusResult:
        self.calls.append("status")
        return StatusResult(PowerStatus.ON, message="Dummy: always on.")


@pytest.fixture(autouse=True)
def register_dummy():
    """Register the dummy driver for each test, then clean up."""
    register_driver(_DummyDriver)
    yield
    unregister_driver("_dummy")


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_builtin_protocols_registered(self) -> None:
        protocols = list_protocols()
        for expected in ("idrac", "ilo", "amt", "ssh_linux", "ssh_windows"):
            assert expected in protocols, f"'{expected}' not registered"

    def test_dummy_registered(self) -> None:
        assert "_dummy" in list_protocols()

    def test_unknown_protocol_raises(self) -> None:
        with pytest.raises(DriverNotFoundError):
            PowerClient(make_host(protocol="no_such_protocol"))

    def test_duplicate_registration_raises(self) -> None:
        with pytest.raises(ValueError, match="already registered"):
            register_driver(_DummyDriver)


# ---------------------------------------------------------------------------
# PowerClient tests
# ---------------------------------------------------------------------------


class TestPowerClient:
    @pytest.mark.asyncio
    async def test_context_manager(self) -> None:
        host = make_host(protocol="_dummy")
        async with PowerClient(host) as client:
            result = await client.reboot()
        assert result.success
        assert result.action == PowerAction.REBOOT

    @pytest.mark.asyncio
    async def test_all_actions_dispatched(self) -> None:
        host = make_host(protocol="_dummy")
        client = PowerClient(host)
        driver: _DummyDriver = client._driver  # type: ignore[assignment]

        for action in ("power_on", "power_off", "power_cycle", "reboot", "shutdown"):
            result = await getattr(client, action)()
            assert result.success, f"{action} should succeed"

        assert driver.calls == [
            "power_on",
            "power_off",
            "power_cycle",
            "reboot",
            "shutdown",
        ]

    @pytest.mark.asyncio
    async def test_status(self) -> None:
        host = make_host(protocol="_dummy")
        async with PowerClient(host) as client:
            result = await client.status()
        assert isinstance(result, StatusResult)
        assert result.status == PowerStatus.ON
        assert result.is_on
        assert not result.is_off

    @pytest.mark.asyncio
    async def test_status_off(self) -> None:
        result = StatusResult(PowerStatus.OFF, message="powered down")
        assert result.is_off
        assert not result.is_on

    @pytest.mark.asyncio
    async def test_repr(self) -> None:
        host = make_host(protocol="_dummy")
        client = PowerClient(host)
        assert "192.168.1.1" in repr(client)
        assert "_dummy" in repr(client)


# ---------------------------------------------------------------------------
# Host / Credentials tests
# ---------------------------------------------------------------------------


class TestHostCredentials:
    def test_credentials_require_secret(self) -> None:
        with pytest.raises(ValueError):
            Credentials(username="admin")

    def test_credentials_with_key(self) -> None:
        creds = Credentials(username="admin", private_key_path="/tmp/id_rsa")
        assert creds.private_key_path == "/tmp/id_rsa"

    def test_host_defaults(self) -> None:
        host = make_host()
        assert host.timeout == 30.0
        assert host.port is None
        assert host.extra == {}


# ---------------------------------------------------------------------------
# PowerResult tests
# ---------------------------------------------------------------------------


class TestPowerResult:
    def test_repr_ok(self) -> None:
        r = PowerResult(PowerAction.REBOOT, success=True, message="done")
        assert "OK" in repr(r)

    def test_repr_failed(self) -> None:
        r = PowerResult(PowerAction.POWER_OFF, success=False, message="err")
        assert "FAILED" in repr(r)


class TestStatusResult:
    def test_repr(self) -> None:
        r = StatusResult(PowerStatus.ON, message="running")
        assert "on" in repr(r)

    def test_is_on_off_helpers(self) -> None:
        assert StatusResult(PowerStatus.ON).is_on
        assert not StatusResult(PowerStatus.ON).is_off
        assert StatusResult(PowerStatus.OFF).is_off
        assert not StatusResult(PowerStatus.OFF).is_on

    def test_unknown_is_neither_on_nor_off(self) -> None:
        r = StatusResult(PowerStatus.UNKNOWN)
        assert not r.is_on
        assert not r.is_off

    def test_all_statuses_exist(self) -> None:
        for s in ("on", "off", "rebooting", "powering_on", "powering_off", "unknown"):
            assert PowerStatus(s)  # raises ValueError if missing
