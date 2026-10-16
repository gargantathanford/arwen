# Development stages

Stages 0–3, the Stage 4 local execution core and a Stage 5 observed-target
mission slice are implemented.
Deterministic policy and local wire/UDP checks pass; Stage 2 live SITL and Stage 3
firmware-specific takeover/failsafe acceptance remain pending. No active flight
commands are enabled.

| Stage | Scope | Completion evidence |
| --- | --- | --- |
| 0: Foundation | Package layout, development configuration, module ownership, architecture documentation. | Package builds and imports; typing and lint configuration are valid; no runtime behavior is introduced. |
| 1: Contracts | Typed state, capabilities, command requests/results, configuration, and interface boundaries. | Dataclass semantics and input validation have behavior tests; fake adapters exercise defined interfaces without hardware or protocol libraries. |
| 2: Passive integration | A first firmware adapter, connection lifecycle, and normalized telemetry. | Simulator telemetry maps to the same contracts; freshness, disconnects, and unsupported capabilities are tested; no mission commands are emitted. |
| 3: Safety and authority | Autonomy permissions, command ownership, cancellation, and controller-specific override semantics. | Simulator scenarios demonstrate authority transfer, stale-command rejection, disconnect behavior, and effective native failsafes. |
| 4: Active control | Supported command translation and acknowledgement/observed-result tracking. | Simulator contract tests cover success, rejection, timeout, interruption, and cancellation before any aircraft operation. |
| 5: Perception and missions | Concrete observations and mission policies using the existing contracts. | Deterministic mission scenarios use recorded or synthetic observations; policies do not import firmware-specific code. |
| 6: Platform hardening | A second firmware adapter, deployment profiles, reproducible tooling, CI, and release policy. | Shared contract tests run against PX4 and ArduPilot configurations; installed distributions, lifecycle behavior, and deployment limits are verified. |

## Stage 4: execution core implemented, live acceptance pending

Stage 3 now implements explicit expiring grants, revocation, operator/failsafe
handling, pending-request cancellation and replay/freshness rejection. See
[safety-authority.md](safety-authority.md) for the behavior and limitations.

Complete the Stage 2 live SITL smoke in [px4-sitl.md](px4-sitl.md), establish
PX4-specific authority observations and validate takeover/native failsafes before
active control. The execution core now performs final local checks, dispatch tracking, stop
handling, acknowledgement correlation and observation-based completion against
a scripted adapter. See [execution.md](execution.md). Live command translation,
firmware cancellation and simulator acceptance remain pending; active PX4 flight
commands remain disabled.

## Stage 5 implemented slice and next work

Typed, timestamped target observations now drive a one-shot observed-target mission.
The application routes its intent through Stage 3 authority and Stage 4 execution
against a scripted backend. See [perception-missions.md](perception-missions.md)
for quality/freshness policy, cancellation and synthetic validation. This does not
implement sensor inference, obstacle avoidance or live aircraft missions.

Stage 6 is platform hardening: reproducible tooling, CI, deployment/release policy
and eventual second-adapter contract validation. Live Stage 2–4 acceptance remains
pending and must be completed before active PX4 use.
