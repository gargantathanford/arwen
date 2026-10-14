# ADR 0001: Typed, modular platform foundation

Status: accepted for the initial scaffold.

## Context

ARWEN must support multiple flight controllers, vehicles, and missions. A single
drone script would entangle hardware I/O with mission decisions and make later
substitution and testing difficult. The current task requires repository
structure only, before any protocol integration or flight behavior.

## Decision

Use Python 3.12+, a `src/` layout, and the `arwen` import package. Package the
platform as `arwen-autonomy` with setuptools and metadata in `pyproject.toml`.
Use nine explicit packages for communication, vehicle contracts, perception,
missions, control, safety, logging, configuration, and application composition.

Keep shared vehicle contracts firmware-independent. Confine future SDK and
protocol details to their integration boundaries. Compose implementations in
`applications`; lower-level packages must not import concrete applications.
Treat the ground control station and flight-controller firmware as external
systems with separate responsibilities.

Require annotated functions and public fields as code is introduced. Configure
mypy in strict mode and Ruff for typing, import, correctness, and modernization
checks. Include `py.typed` for downstream type checking. Choose dataclasses for
concrete value records when their semantics are defined.

Include no runtime dependencies, artificial data models, stub functions,
application entry points, or empty behavior tests in this stage. Package
initializers document ownership without triggering runtime work.

## Consequences

- Development imports require an installed package; tests must not hide
  packaging errors by altering import paths.
- Adapter-specific assumptions cannot silently become mission assumptions.
- Applications must eventually coordinate operator authority and controller
  failsafes; a generic transport layer cannot solve these semantics by itself.
- Typing configuration establishes a development baseline. It does not prove
  freshness, valid units, command completion, or flight safety.
- Dependency rules are documented now and will need automated enforcement once
  implementations introduce internal imports.
- Timing, concurrency, plugin loading, persistence, and deployment frameworks
  remain open decisions until concrete requirements justify them.

## Alternatives considered

A flat repository script is unsuitable for a reusable platform. A firmware-
specific core would prevent independent mission reuse. Multiple services or
distributions would add deployment and version-coordination work before the
interfaces are known. Large framework base classes would encode assumptions
without implementations to test them against.
