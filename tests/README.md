# Test strategy

This directory is intentionally free of Python tests at the structure-only
stage. There is no behavior to validate yet. Pytest is configured for this
directory, and a run currently exits with code 5 because it collects no tests.

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
