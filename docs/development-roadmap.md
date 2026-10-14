# Development stages

Only Stage 0 is included in this repository. Later stages describe proposed work
and its acceptance criteria.

| Stage | Scope | Completion evidence |
| --- | --- | --- |
| 0: Foundation | Package layout, development configuration, module ownership, architecture documentation. | Package builds and imports; typing and lint configuration are valid; no runtime behavior is introduced. |
| 1: Contracts | Typed state, capabilities, command requests/results, configuration, and interface boundaries. | Dataclass semantics and input validation have behavior tests; fake adapters exercise defined interfaces without hardware or protocol libraries. |
| 2: Passive integration | A first firmware adapter, connection lifecycle, and normalized telemetry. | Simulator telemetry maps to the same contracts; freshness, disconnects, and unsupported capabilities are tested; no mission commands are emitted. |
| 3: Safety and authority | Autonomy permissions, command ownership, cancellation, and controller-specific override semantics. | Simulator scenarios demonstrate authority transfer, stale-command rejection, disconnect behavior, and effective native failsafes. |
| 4: Active control | Supported command translation and acknowledgement/observed-result tracking. | Simulator contract tests cover success, rejection, timeout, interruption, and cancellation before any aircraft operation. |
| 5: Perception and missions | Concrete observations and mission policies using the existing contracts. | Deterministic mission scenarios use recorded or synthetic observations; policies do not import firmware-specific code. |
| 6: Platform hardening | A second firmware adapter, deployment profiles, reproducible tooling, CI, and release policy. | Shared contract tests run against PX4 and ArduPilot configurations; installed distributions, lifecycle behavior, and deployment limits are verified. |

## Next stage: define contracts before protocol code

Identify the smallest state fields, capabilities, and action types needed by the
first mission. Specify units, coordinate frames, freshness, missing-data
semantics, request expiration, and error results before choosing implementation
names or function signatures.

Introduce appropriately typed dataclasses and protocols in their owning
packages. Add configuration validation and a fake adapter to verify the
contracts. Inject a clock where timing matters. Keep this stage independent of
MAVLink, an SDK, a simulator process, and physical hardware.

Decide the command-authority model before adding repeated control outputs. A
future adapter must report its supported capabilities rather than implying all
vehicles can satisfy every generic request.
