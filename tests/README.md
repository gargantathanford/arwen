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
