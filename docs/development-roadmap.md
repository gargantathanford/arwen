# Development stages

Stages 0 and 1 plus the Stage 2 passive PX4 implementation are present. Stage 2
has deterministic and local wire/UDP validation; live simulator acceptance is
still pending. Later stages describe proposed work and acceptance criteria.

| Stage | Scope | Completion evidence |
| --- | --- | --- |
| 0: Foundation | Package layout, development configuration, module ownership, architecture documentation. | Package builds and imports; typing and lint configuration are valid; no runtime behavior is introduced. |
| 1: Contracts | Typed state, capabilities, command requests/results, configuration, and interface boundaries. | Dataclass semantics and input validation have behavior tests; fake adapters exercise defined interfaces without hardware or protocol libraries. |
| 2: Passive integration | A first firmware adapter, connection lifecycle, and normalized telemetry. | Simulator telemetry maps to the same contracts; freshness, disconnects, and unsupported capabilities are tested; no mission commands are emitted. |
| 3: Safety and authority | Autonomy permissions, command ownership, cancellation, and controller-specific override semantics. | Simulator scenarios demonstrate authority transfer, stale-command rejection, disconnect behavior, and effective native failsafes. |
| 4: Active control | Supported command translation and acknowledgement/observed-result tracking. | Simulator contract tests cover success, rejection, timeout, interruption, and cancellation before any aircraft operation. |
| 5: Perception and missions | Concrete observations and mission policies using the existing contracts. | Deterministic mission scenarios use recorded or synthetic observations; policies do not import firmware-specific code. |
| 6: Platform hardening | A second firmware adapter, deployment profiles, reproducible tooling, CI, and release policy. | Shared contract tests run against PX4 and ArduPilot configurations; installed distributions, lifecycle behavior, and deployment limits are verified. |

## Next stage: safety and authority

Define production authority grants, operator takeover, cancellation and stale
command rejection before active output. Validate firmware-specific behavior in
simulator contract scenarios. Complete the opt-in Stage 2 live SITL smoke in
[px4-sitl.md](px4-sitl.md) before claiming simulator acceptance.
