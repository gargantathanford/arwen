# ADR 0007: Locked development and distribution checks

Status: accepted for the first Stage 6 hardening slice. Second-adapter, live
simulator, deployment and release acceptance are still pending.

Retain the existing optional extras and dependency compatibility ranges. Add a
universal uv.lock and a build dependency group so development tools and build
backends resolve to exact artifacts with recorded hashes. Pin the PEP 517 backend in both build-system requirements and the build group,
so editable installation cannot float to a different backend. Pin the bootstrap uv
version to 0.12.23 in CI/docs. Use uv sync --locked and explicit extras/groups;
normal refresh must not silently rewrite the lock. Python minor versions and OS
runner images are a tested/configured matrix, not byte-identical system images.

CI covers Python 3.12/3.13 on Linux with core and MAVLink profiles and Python 3.12
on macOS with both profiles. Pin GitHub actions to verified tag commit SHAs, use
read-only repository permission, cancel superseded runs and limit job duration.
CI never installs a simulator, reads flight credentials, publishes, pushes or
performs vehicle operations. A configured workflow is not a recorded hosted pass.

Both profiles run lint, formatting, strict source/tool typing, static package
boundary checks and all default tests.
The core profile intentionally skips the two optional wire/UDP tests; the MAVLink
profile runs them. Build using locked installed backend dependencies with
python -m build --no-isolation, then verify a wheel in a clean temporary venv
outside the checkout. Rebuild the sdist into another wheel and repeat the import,
metadata, typing-marker, optional-dependency and synthetic-application checks.
Neither build backend isolation nor editable imports should hide unlocked build
dependencies or an incomplete sdist. Hash verification remains enabled.

Ship the lock, tooling and tests with the sdist so the validation workflow can be
reproduced. No package publication workflow or license selection is invented.
Document development/test profiles, lock refresh and explicit release blockers.
This is not yet deployment on an aircraft, support for ArduPilot, a reproducible
OS image or full Stage 6 completion.
