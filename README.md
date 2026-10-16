# ARWEN

ARWEN is a reusable UAV autonomy middleware platform. It is intended to connect
mission behavior, perception, control requests, and safety policy to different
flight controllers and vehicle configurations. PX4 and ArduPilot are initial
integration targets; neither defines the core application model.

Stages 1–3 and the Stage 4 execution core provide typed firmware-neutral contracts, a passive PX4 UDP telemetry
adapter, and local safety/authority policy. The terminal monitor observes heartbeat health and local
NED position with explicit timing quality. Every vehicle request is rejected as
unsupported. Local authority grants, takeover revocation and cancellation are implemented;
there is no active flight-command path or mission execution. This is not a flight-ready system.

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

Contracts are defined in `arwen.vehicle.contracts` and timing configuration in
`arwen.config.models`. Package initializers remain free of eager imports. A
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

Run the deterministic Stage 1 behavior and fake-adapter tests:

```bash
python -m pytest
```

The core has no runtime dependencies. Tooling is installed through the `dev`
extra. No simulator, flight-controller checkout, or camera library is needed to
install or import this scaffold.

## Documentation

- [Architecture and dependency rules](docs/architecture.md)
- [Initial architecture decision](docs/decisions/0001-platform-foundation.md)
- [Development guidelines](CONTRIBUTING.md)
- [Development roadmap](docs/development-roadmap.md)
- [Test strategy](tests/README.md)

Stage 3 local safety/authority behavior is implemented. See the
[safety and authority guide](docs/safety-authority.md) and
[ADR 0004](docs/decisions/0004-authority-and-cancellation.md).
Live PX4 SITL and firmware-specific takeover/failsafe acceptance remain pending.
Live Stage 4 active control must wait for those checks. The firmware-neutral
execution core and scripted example are implemented; see the
[execution guide](docs/execution.md) and [ADR 0005](docs/decisions/0005-execution-lifecycle.md).

Run the next execution example with:

```bash
python -m arwen.applications.execution_demo
```

Run the synthetic local scenario with:

```bash
python -m arwen.applications.authority_demo
```

## Passive PX4 monitor

```bash
python -m pip install -e '.[mavlink]'
python -m arwen.applications.telemetry --bind-host 127.0.0.1 --bind-port 14540 \
  --system-id 1 --component-id 1
```

See the [PX4 SITL guide](docs/px4-sitl.md) for installation, VM networking,
QGroundControl concurrency, timing/frame limitations and the opt-in live smoke
procedure. See [ADR 0003](docs/decisions/0003-passive-px4.md) for lifecycle and
TIMESYNC-only transmission policy. Core installation does not require pymavlink.

See [ADR 0002](docs/decisions/0002-position-contracts.md) for the first contract
semantics and remaining integration decisions.
