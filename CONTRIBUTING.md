# Development guidelines

## Scope and change discipline

ARWEN is a reusable platform. Keep competition rules, individual airframes,
mission-specific thresholds, and hardware assumptions outside generic core
behavior. Application configuration and mission definitions may specialize the
platform without making those choices universal defaults.

Implement the smallest complete behavior needed by a concrete use case. Avoid
empty classes, placeholder functions, speculative methods, catch-all `utils`
modules, and interfaces with no defined consumer. Record cross-module decisions
in `docs/decisions/` before introducing a new architectural dependency.

The current stage includes initial typed contracts and deterministic tests. Future changes must have their own scope
and acceptance criteria.

## Python and typing

- Require Python 3.12+ and modern type syntax.
- Annotate every function and method, including `__init__`, tests, callbacks,
  return values, and public fields when they are introduced.
- Annotate module-level configuration and shared state explicitly. Let local
  variables use type inference where their meaning is unambiguous.
- Prefer precise types over `Any`, unstructured dictionaries, and stringly typed
  state. Validate untrusted input at the external boundary.
- Use `Protocol` when a concrete dependency needs a structural interface;
  introduce abstract base classes only when shared behavior warrants them.
- Use dataclasses for value records and configuration. Prefer `frozen=True` for
  snapshots and `slots=True` where appropriate. Mutable collection defaults need
  a factory; a frozen dataclass does not make nested mutable objects immutable.
- Document public contracts with units, coordinate frames, timestamps, validity,
  and error behavior. A type hint alone does not describe these semantics.

## Dependencies and lifecycle

Follow the allowed imports in [architecture.md](docs/architecture.md). External
protocol and sensor libraries belong to the package that owns their integration.
Add optional dependencies only when a real adapter requires them. Do not import
an optional adapter from a package initializer.

Use absolute package imports such as `arwen.vehicle`. ARWEN's `logging` package
does not replace the standard library: use `import logging` for that library and
`import arwen.logging` for ARWEN's package. Never add `src/arwen` to an import path.

Pass dependencies explicitly at application composition. Library imports must
not open sockets, connect to vehicles, load models, read environment variables,
configure global logging, or start threads or tasks. Connection and task
lifecycles need explicit ownership and shutdown behavior.

An asynchronous runtime, event bus, plugin loader, and dependency-injection
framework are not selected yet. Choose them only after timing, backpressure,
cancellation, and consumer requirements are known.

## Safety and observability

Never infer valid state from missing telemetry. When contracts are introduced,
represent freshness, missing values, capability limits, and command outcomes
explicitly. Distinguish a submitted request, a controller acknowledgement, and
an observed physical result.

Firmware-specific mode changes and manual override semantics need adapter
contract tests. Autonomy safety policy complements flight-controller failsafes;
it must not claim to replace them. Decide how stale commands are cancelled
before adding repeated control output.

Record actionable diagnostic context without credentials or unnecessary private
data. Logging should expose events and decisions without becoming a dependency
that owns flight behavior.

## Validation and review

From an installed development environment, run:

```bash
python -m ruff check .
python -m ruff format --check .
python -m mypy
python -m build
```

Run `python -m pytest` for the Stage 1 behavior tests. A zero-test run is not
evidence that behavior has been tested.
See [tests/README.md](tests/README.md) for future testing categories.

For functional changes, add tests for externally meaningful behavior and
failure cases. Prefer fake adapters and injected clocks over hardware, sleeps,
and public network connections. Check installed wheel imports outside the
repository when packaging changes; editable imports alone cannot establish that
the distribution is complete.

In a change description, explain the resulting behavior, boundary affected,
validation performed, and material limitations. Do not declare a feature
flight-ready based only on unit tests. Record the simulator, firmware version,
vehicle configuration, and controlled validation conditions for later integration
work.

Development dependency ranges are compatibility bounds, not a reproducible
environment lock. Establish a tested toolchain lock and CI matrix before release.
Choose a license and release metadata before external distribution; this scaffold
does not assign a license or claim a package-registry name.
