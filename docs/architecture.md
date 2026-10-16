# Architecture

Status: Stage 1 contracts, Stage 2 passive PX4 monitoring, and Stage 3 local
safety/authority policy and the Stage 4 local execution core are implemented. Communication owns telemetry lifecycle
and normalization; safety evaluates explicit evidence; control owns leases,
pending requests and cancellation; applications owns composition. Control also owns single-request dispatch tracking against the optional
ExecutionAdapter protocol. There is no live PX4 command dispatch path. Live firmware-specific takeover/failsafe acceptance remains
pending. See ADRs 0003–0004 for timing, frame and authority limitations.

## Control-system separation

The flight controller owns stabilization, actuator outputs, flight modes, state
estimation, and its native failsafes. ARWEN owns mission-level decisions and
requests actions through an adapter. The ground control station owns the
human-facing setup and supervision interface. None of these roles implies
exclusive command authority merely because a process is connected.

The external system may also include an RC pilot, sensors, a companion computer,
and payload mechanisms. Aircraft- and mission-specific integrations belong at
explicit boundaries rather than being imported into generic mission policy.

Different firmware can expose similar protocols while implementing different
mode semantics, acknowledgements, capability limits, and failsafes. A shared
transport is not proof that PX4 and ArduPilot are interchangeable. Future adapters
must normalize only capabilities they can support and report unsupported
requests explicitly.

## Intended information flow

Vehicle telemetry enters through `communication`, is normalized into `vehicle`
contracts, and becomes a timestamped state snapshot. Sensor interpretation in
`perception` produces observations rather than flight commands. `missions`
consumes state and observations to produce mission intent. `control` translates
that intent, consults `safety`, and produces eligible vehicle requests. A
communication adapter submits those requests to the flight controller.

`applications` composes these components and owns their runtime lifecycles.
Configuration and diagnostic policy support each layer. The direction of data
flow does not require each producer to import its consumer.

## Dependency rules

This table lists allowed internal import targets, not required dependencies.
Standard-library use is permitted throughout. Applications may wire a callable
or protocol dependency without making a lower-level package import a concrete
implementation from another package.

| Package | Allowed internal import targets | Boundary rule |
| --- | --- | --- |
| `vehicle` | None | Shared vehicle contracts stay independent of firmware, transport, mission policy, and I/O. |
| `config` | None | Configuration foundations do not import runtime components. |
| `logging` | `config` | Diagnostics do not import mission, control, or transport implementations. |
| `communication` | `vehicle`, `config`, `logging` | Adapters normalize external data; they do not decide mission behavior. |
| `perception` | `vehicle`, `config`, `logging` | Perception returns observations; it does not access flight-controller adapters. |
| `safety` | `vehicle`, `config`, `logging` | Policy evaluates contracts; it does not send transport commands. |
| `control` | `vehicle`, `safety`, `config`, `logging` | Control works against vehicle contracts, never a concrete firmware SDK. |
| `missions` | `vehicle`, `perception`, `config`, `logging` | Missions express intent; the application routes it through control and safety. |
| `applications` | All ARWEN packages | The composition boundary selects concrete integrations and owns startup and shutdown. |

Place an interface with the abstraction that owns its meaning. Firmware-neutral
vehicle interaction contracts belong in `vehicle`; sensor contracts belong in
`perception`. Implementations may satisfy those interfaces through structural
typing. Avoid moving every shared type into a generic core package.

The first contracts must clarify how mission intent and vehicle requests differ
without creating a dependency cycle. Do not infer a public API from these folder
names alone. Changes to this import policy need a decision record; future
import-boundary checks should enforce the policy as implementations grow.

## Typed contracts and data semantics

Use dataclasses for concrete value records and configuration when the first
consumers exist. Prefer immutable state and observation snapshots. Protocols
describe substitutable behavior; they are not a reason to invent unused methods.

Specify SI units where appropriate, angular conventions, coordinate frames,
measurement timestamps, receipt timestamps, and source identity. Use monotonic
time for elapsed intervals and explicit wall-clock timestamps for audit records.
Represent unknown or stale data distinctly from valid zero values. Perception
contracts must describe uncertainty and validity rather than exposing raw
library-specific result objects to missions.

Command contracts need request identity, capabilities, authority, expiration,
acknowledgement semantics, and error results. A command being accepted does not
prove that the intended physical action occurred.

## Command ownership and failure handling

Before active control is implemented, define how ARWEN yields authority to an
operator or controller failsafe and prevents previously queued commands from
reasserting control. Evaluate firmware-specific takeover behavior in adapter
tests. Stopping ARWEN must not be the only way an aircraft can be recovered.

Safety policy can restrict autonomy requests, but safety decisions made in a
companion process cannot replace controller-enforced limits or hardware safety
mechanisms. Later implementations need explicit handling for stale state,
lost links, unavailable capabilities, interrupted missions, and failed shutdown.

## Runtime and packaging

The platform begins as one Python distribution with nine logical packages. This
provides clear boundaries without early process, RPC, or deployment complexity.
Select process boundaries and concurrency mechanisms after profiling the real
perception and control requirements.

The `src/` layout separates importable code from repository tooling and
documentation. Setuptools discovers only `arwen` and its subpackages; the
`py.typed` marker is included in the wheel. Keep `__init__.py` files small and
free of eager imports. The core has no runtime dependencies. The optional `mavlink` extra supplies
pymavlink for the explicitly started terminal telemetry monitor.

## References

- [Python Packaging User Guide: src layout](https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/)
- [Setuptools: pyproject configuration](https://setuptools.pypa.io/en/stable/userguide/pyproject_config.html)
- [Ruff configuration](https://docs.astral.sh/ruff/configuration/)
- [Mypy configuration](https://mypy.readthedocs.io/en/stable/config_file.html)
- [Pytest integration practices](https://docs.pytest.org/en/stable/explanation/goodpractices.html)
