# ARWEN

ARWEN is a reusable UAV autonomy middleware platform. It is intended to connect
mission behavior, perception, control requests, and safety policy to different
flight controllers and vehicle configurations. PX4 and ArduPilot are initial
integration targets; neither defines the core application model.

This repository currently contains the initial package structure, development
configuration, and architectural decisions. The package has no runtime behavior,
flight logic, MAVLink implementation, hardware access, or application entry point.
It is an early platform scaffold, not a flight-ready system.

## System responsibilities

| System | Responsibility |
| --- | --- |
| Flight controller | Runs vehicle stabilization, estimation, actuator control, supported flight modes, and native failsafes. Its firmware remains responsible for these time-sensitive functions. |
| ARWEN autonomy layer | Will interpret observations, execute mission policies, request supported vehicle actions, and apply autonomy-level safety policy. It acts through explicit flight-controller interfaces. |
| Ground control station | Provides the human-facing interface for vehicle setup, mission supervision, telemetry, and operator commands. It remains an external system; a custom interface is not required by this scaffold. |

The ground control station and ARWEN may both communicate with the flight
controller. Their command authority must be coordinated in later integration
work. Manual takeover and flight-controller failsafes must remain effective even
when ARWEN stops, disconnects, or requests an unsupported operation.

ARWEN may run on an onboard companion computer or in another supported runtime.
The core package does not select a computer, radio, camera, simulator, or operating
system. Deployment choices belong to application configuration.

## Package boundaries

The import package is `arwen`; its distribution name is `arwen-autonomy`.

| Directory | Intended ownership |
| --- | --- |
| `src/arwen/communication/` | Transport integration, flight-controller adapters, protocol translation, connection lifecycle. |
| `src/arwen/vehicle/` | Firmware-independent vehicle state, capabilities, coordinate conventions, and shared vehicle contracts. |
| `src/arwen/perception/` | Sensor interpretation and observations with explicit timestamps, frames, and uncertainty. |
| `src/arwen/missions/` | Reusable mission policies and progress; consumes observations and expresses intent. |
| `src/arwen/control/` | Translates intent into supported vehicle requests and coordinates command authority. |
| `src/arwen/safety/` | Autonomy constraints, health assessment, and permission decisions for requested actions. |
| `src/arwen/logging/` | Diagnostic and event-recording policy, without configuring logging on import. |
| `src/arwen/config/` | Explicit configuration models, loading, and validation. |
| `src/arwen/applications/` | Runtime composition, dependency injection, and deployment-specific entry points. |
| `tests/` | Future behavior, contract, and integration tests. |
| `docs/` | Architecture, decision records, and development stages. |

Each Python package currently contains only a descriptive `__init__.py`. A
`py.typed` marker identifies the package as providing inline typing. Data models
and interfaces will be added when their first concrete contracts are defined.

## Design philosophy

- Keep flight-controller details at the communication boundary. Mission and
  perception code must not depend on firmware-specific messages or parameters.
- Separate policy from mechanisms: missions choose intent, control translates
  it, safety determines permission, and communication performs external I/O.
- Prefer small, typed contracts and composition over global state, large base
  classes, or speculative abstractions.
- Use dataclasses for suitable configuration and value records. Use immutable
  snapshots for shared observations; do not treat mutable hardware sessions as
  dataclass value objects.
- Make units, frames, freshness, capabilities, and command ownership explicit.
- Keep import-time behavior free of connections, device access, background
  tasks, and logging configuration.
- Design for simulation and deterministic tests before hardware integration.

## Development setup

Use Python 3.12 or newer. From the repository root in a POSIX shell:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

The `src/` layout requires installation for normal imports. Do not patch
`sys.path` or set `PYTHONPATH=src` to bypass the package configuration.

```bash
python -m ruff check .
python -m ruff format --check .
python -m mypy
python -m build
```

There are deliberately no behavior tests yet. `python -m pytest` currently
reports no collected tests and exits with code 5. Add tests with the first
implemented behavior; do not add empty tests to manufacture a passing suite.

The core has no runtime dependencies. Tooling is installed through the `dev`
extra. No simulator, flight-controller checkout, or camera library is needed to
install or import this scaffold.

## Documentation

- [Architecture and dependency rules](docs/architecture.md)
- [Initial architecture decision](docs/decisions/0001-platform-foundation.md)
- [Development guidelines](CONTRIBUTING.md)
- [Development roadmap](docs/development-roadmap.md)
- [Test strategy](tests/README.md)

The next stage is to define typed vehicle state, capability, command-result, and
configuration contracts and test them using fake adapters. Protocol integration
and flight behavior follow those contracts.
