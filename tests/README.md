# Test strategy

`test_contracts.py` exercises initial Stage 1 contracts with a deterministic fake
adapter. It covers validation, freshness, frame agreement, request expiry,
authority revocation, unsupported capabilities, replay rejection and submission
without implied physical movement. No simulator or hardware is accessed.

Add tests alongside the first implemented behavior. Annotate test functions and
fixtures. Import the installed `arwen` package, never code through a modified
`sys.path`. Use fake dependencies and injected clocks for unit and contract tests.

| Category | Intended evidence |
| --- | --- |
| Unit | Value-record validation, configuration, mission transitions, safety policy, and request translation have defined observable behavior. |
| Contract | Substitute adapters preserve normalized state, capabilities, result semantics, and authority behavior. |
| Integration | A specified simulator and firmware configuration exercises actual protocol and lifecycle behavior. |
| Scenario | Interruptions, stale state, unavailable sensors, operator takeover, and link loss produce defined outcomes. |
| Packaging | A built wheel imports outside the repository and contains all intended packages and typing metadata. |

Introduce directories and markers for these categories only when tests require
them. Hardware and simulator tests must become explicit opt-in activities; the
default test suite must not connect to a vehicle. Tests must describe behavior
and meaningful failure cases, not merely repeat an implementation or check that
a placeholder exists.

Stage 2 tests use fake transport and injected clocks (`test_px4.py`), plus real
MAVLink encoding and loopback UDP (`test_mavlink.py`). Install `.[dev,mavlink]`
to run wire tests; with core tooling alone the two optional wire tests skip.
No default test uses aircraft, simulators, public networks or timing sleeps.
Installed-wheel checks verify imports without pymavlink and the diagnostic for a
missing extra. Live SITL is opt-in and has not been run; see docs/px4-sitl.md.

Stage 3 tests (`test_authority.py`) cover explicit leases, forged/pre-grant
requests, owner transfer, takeover/failsafe/disconnect revocation, cancellation,
source/frame changes, exact expiry/freshness boundaries, delayed recovery, clock
errors, bounded replay history and the synthetic application. They exercise local
policy without flight commands. Live firmware authority/failsafe acceptance has
not been run; see docs/safety-authority.md.

Stage 4 tests (`test_execution.py`) exercise the scripted execution boundary:
submission vs acknowledgement vs measured completion, post-submission telemetry,
ACK correlation, exact deadlines, takeover/loss, cancellation, send/stop failures,
final admission checks, immutable released requests and resource limits. PX4
remains passive. No live command translation or simulator acceptance is claimed.

Stage 5 tests (`test_missions.py`) cover observation validation, one-shot policy,
confidence/uncertainty and exact freshness limits, duplicate/reordered/conflicting
data, frame/session changes, frozen targets, intent admission deadlines, sensor
loss and takeover, and the synthetic observation-to-execution application. Tests
use no camera, model, public network or hardware. See docs/perception-missions.md.
