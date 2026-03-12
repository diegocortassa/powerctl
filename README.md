# powerctl

Remote power management library for workstations and servers (iDRAC, iLO, AMT, SSH).

## Project Overview

`powerctl` is a Python library designed to provide a unified interface for controlling the power state of various workstations and servers. It supports multiple protocols through a driver-based architecture, allowing for easy extension via drivers and concurrency via `asyncio`.

## Supported protocols

| Protocol ID    | Technology              | Notes                                |
|----------------|-------------------------|--------------------------------------|
| `idrac`        | Dell iDRAC (Redfish)    | iDRAC 8 / 9                          |
| `ilo`          | HP iLO (Redfish)        | iLO 4 / 5 / 6                        |
| `amt`          | Intel AMT (WS-Man)      | WS-Management SOAP over HTTP(S)      |
| `ssh_linux`    | SSH Linux               | Uses `asyncssh` or `ssh` CLI         |
| `ssh_windows`  | SSH / WinRM Windows     | PowerShell via SSH or WinRM          |

## Quick start

### Installation
```bash
pip install "powerctl[all]"           # everything (recommended)
pip install powerctl                  # core, no optional deps
pip install "powerctl[ssh]"           # + asyncssh for SSH drivers
pip install "powerctl[winrm]"         # + pywinrm for WinRM driver
```

```python
import asyncio
from powerctl import PowerClient, Host, Credentials

host = Host(
    hostname="192.168.1.10",
    protocol="idrac",
    credentials=Credentials(username="root", password="calvin"),
)

async def main():
    async with PowerClient(host) as client:
        result = await client.reboot()
        print(result)  # <PowerResult action=reboot status=OK ...>

asyncio.run(main())
```

## Per-protocol examples

### Dell iDRAC

```python
Host(
    hostname="192.168.1.10",
    protocol="idrac",
    credentials=Credentials(username="root", password="calvin"),
)
```

### HP iLO

```python
Host(
    hostname="192.168.1.20",
    protocol="ilo",
    credentials=Credentials(username="Administrator", password="hunter2"),
)
```

### Intel AMT

```python
Host(
    hostname="192.168.1.30",
    protocol="amt",
    credentials=Credentials(username="admin", password="AMTs3cret!"),
    extra={"tls": True},         # use port 16993 (HTTPS)
)
```

### SSH Linux

```python
Host(
    hostname="192.168.1.40",
    protocol="ssh_linux",
    credentials=Credentials(username="sysadmin", private_key_path="/home/me/.ssh/id_ed25519"),
    extra={"sudo": True},
)
```

### SSH/WinRM Windows

```python
# Via SSH (OpenSSH Server must be installed on Windows)
Host(
    hostname="192.168.1.50",
    protocol="ssh_windows",
    credentials=Credentials(username="Administrator", password="W1nd0ws!"),
    extra={"transport": "ssh"},
)

# Via WinRM (requires pip install powerctl[winrm])
Host(
    hostname="192.168.1.50",
    protocol="ssh_windows",
    credentials=Credentials(username="Administrator", password="W1nd0ws!"),
    extra={"transport": "winrm", "https": True},
)
```

## Bulk operations

```python
from powerctl import reboot_all, run_action_all, Host, Credentials

hosts = [
    Host(hostname=f"10.0.0.{i}", protocol="idrac",
         credentials=Credentials(username="root", password="calvin"))
    for i in range(1, 21)
]

async def main():
    results = await reboot_all(hosts, max_concurrent=5)
    for r in results:
        print(r)
```

## Building and development setup

### Setup
The project uses `setuptools` and `pyproject.toml`.

```bash
# Create a virtual environment
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip

# install build tool
pip install --upgrade build

# Install in editable mode with all dependencies
pip install -e ".[all,dev]"
```

### Testing
Tests are located in the `tests/` directory and use `pytest` with `pytest-asyncio`.

```bash
pytest
```

### Linting and Type Checking
The project enforces strict typing and linting standards.

```bash
# Linting and formatting
ruff check .
ruff format .

# Type checking
python3 -m pip install types-requests
mypy src/powerctl
```

## Building 
```bash
python -m build
```

## Writing a custom driver

```python
from powerctl import register_driver, BaseDriver, PowerAction, PowerResult
from powerctl.core import Host

@register_driver
class IpmiDriver(BaseDriver):
    """Example IPMI driver using ipmitool CLI."""

    protocol = "ipmi"

    async def power_on(self) -> PowerResult:
        # call ipmitool here ...
        return PowerResult(PowerAction.POWER_ON, success=True)

    async def power_off(self) -> PowerResult: ...
    async def power_cycle(self) -> PowerResult: ...
    async def reboot(self) -> PowerResult: ...
    async def shutdown(self) -> PowerResult: ...
```

Once decorated with `@register_driver`, the driver is immediately
available to `PowerClient` by its `protocol` string.

## Running tests

```bash
pip install "powerctl[dev]"
pytest
```

## Layout

```
powerctl/
├── __init__.py          # Public API surface
├── client.py            # PowerClient facade + bulk helpers
├── core/
│   ├── base.py          # BaseDriver ABC, PowerAction enum, PowerResult
│   ├── host.py          # Host & Credentials dataclasses
│   ├── registry.py      # @register_driver decorator + driver factory
│   └── exceptions.py    # Exception hierarchy
└── drivers/
    ├── _redfish.py      # Shared Redfish HTTP helpers
    ├── idrac.py         # Dell iDRAC
    ├── ilo.py           # HP iLO
    ├── amt.py           # Intel AMT
    ├── ssh_linux.py     # SSH Linux
    └── ssh_windows.py   # SSH / WinRM Windows
```

Notes:

- **Strategy + Registry pattern** — drivers are registered by `protocol` string; `build_driver(host)` picks the right one at runtime.
- **Abstract Base Class** — `BaseDriver` enforces the interface; `mypy --strict` will catch missing methods.
- **Zero mandatory dependencies** — the stdlib covers iDRAC/iLO/AMT; SSH and WinRM libs are opt-in extras.
- **Async-first** — all operations are `async`, enabling high-throughput bulk management with `asyncio.gather`.
- **Dataclasses for config** — `Host` and `Credentials` are frozen.
