# Stage 6: reproducible development and package validation

The first hardening slice adds a hashed dependency lock, pinned build backend,
CI matrix, static package-boundary checks and installed-distribution verification.
It does not add ArduPilot, aircraft deployment or live PX4 commands. The remaining
live Stage 2–4 acceptance checks still apply.

## Bootstrap and locked development

Use Python 3.12 or 3.13 and **uv 0.12.23**. Keep the bootstrap tool outside the
project environment so profile synchronization cannot uninstall it. For example,
in a POSIX shell:

```bash
python3.12 -m venv "$HOME/.venvs/arwen-tools"
"$HOME/.venvs/arwen-tools/bin/python" -m pip install uv==0.12.23
export PATH="$HOME/.venvs/arwen-tools/bin:$PATH"
cd /path/to/arwen
uv sync --locked --extra dev --extra mavlink --group build --python 3.12
source .venv/bin/activate
```

In a restricted cloud home, use a separate `/tmp/arwen-tools` bootstrap directory
and set `UV_CACHE_DIR=/tmp/arwen-uv-cache` if the default cache is not writable.
This cloud already provides the pinned uv version. Do not create another checkout
or worktree just to prepare a profile.

`uv.lock` records exact package versions and artifact hashes for the declared
extras and build group. `--locked` rejects metadata changes that require a new
lock instead of silently resolving them. Hash and TLS verification remain enabled.
The setuptools backend is also pinned in `build-system.requires`, so editable
installation's isolated build does not float independently of the lock. Build
validation uses the locked installed backend via `--no-isolation`; it does not
turn off artifact verification.

The lock is a dependency/environment resolution, not a bit-identical OS image.
Python patch versions, OS libraries, timestamps and runner images can differ.
The package still declares Python >=3.12; Python versions outside the configured
3.12/3.13 matrix have not been validated here. Standard pip extras remain usable,
but their compatibility ranges alone are not the locked workflow.

## Profiles

From the checkout, choose one profile:

| Profile | Command | Intended use |
| --- | --- | --- |
| Core development | `uv sync --locked --extra dev --group build` | Policy, missions, unit tests and package builds without pymavlink. |
| MAVLink development | `uv sync --locked --extra dev --extra mavlink --group build` | Full default suite including local encoded-MAVLink/UDP tests. |
| Passive monitor | `uv sync --locked --extra mavlink --no-default-groups` | Minimal runtime for the read-only PX4 monitor; no development/build tools. |

Synchronization removes unselected packages; switch back to a development profile
before running development checks. Use `--python 3.12` or `--python 3.13` when
creating the environment. Different interpreters need distinct environments;
`UV_PROJECT_ENVIRONMENT=/tmp/arwen-py313` can keep a secondary environment outside
the checkout. Profile selection does not grant vehicle authority.

For the passive monitor, use the existing [PX4 guide](px4-sitl.md) and explicit
bind/source settings. No system service, aircraft startup or credentials are
installed by these profiles. Synthetic demos remain available in the core wheel.

## Run the validation workflow

From a locked development/build environment:

```bash
python -m ruff check .
python -m ruff format --check .
python -m mypy
python -m mypy --strict tools
python tools/check_imports.py
python -m pytest
python -m build --no-isolation
python tools/check_distribution.py dist
```

Use an output directory containing exactly one newly built wheel and sdist. If old
release versions remain in `dist`, choose a fresh output directory and pass it to
both commands. Do not mix artifacts from different versions or profiles.

The import checker enforces the documented static package edges, absolute imports
and protocol-library ownership. It also checks literal dynamic imports. It is not
a sandbox or a proof against dynamically constructed imports or malicious code.
Tests demonstrate that forbidden boundaries fail the check.

The distribution checker creates temporary clean environments outside the checkout
using the pinned uv tool. Installs are offline, core-only, without dependency or
index resolution. Python isolated mode prevents PYTHONPATH/user-site shortcuts.
It imports all packages/modules without opening sockets or starting workers,
checks typing and metadata, verifies the missing-MAVLink diagnostic and runs all
three scripted demonstrations. It then extracts the sdist with safe extraction,
rebuilds a wheel using the locked build environment, and repeats those checks.
Temporary files are removed on completion or failure. This checks functionality
and packaging completeness, not byte-for-byte identical wheel archives.

## CI scope and evidence

`.github/workflows/ci.yml` runs on pushes, pull requests and manual invocation.
It has read-only repository permission, pinned checkout/setup actions, no retained
checkout credentials, cancellation of superseded runs and a 15-minute job limit.
It never publishes artifacts, tags releases or controls vehicles.

Configured matrix:

| Runner | Python | Profiles |
| --- | --- | --- |
| Ubuntu latest | 3.12 and 3.13 | Core and MAVLink |
| macOS latest | 3.12 | Core and MAVLink |

Core runs intentionally skip the optional real-wire tests. MAVLink runs must
import the required dialect before testing, so a missing extra cannot quietly
turn those tests into skips. Each job runs policy, lint, formatting, type,
package-boundary, build and distribution-roundtrip checks.

Linux profiles were exercised locally using Python 3.12.14 and 3.13.5. macOS and
hosted GitHub Actions have **not** run as part of this task; a workflow file is not
a hosted CI result. See the chat report for final test counts.

## Refresh dependencies deliberately

1. Update compatibility bounds or build-backend pins only for a defined reason.
   Keep the backend version in `build-system.requires` and the build group aligned.
2. Run `uv lock --upgrade` with uv 0.12.23 against the public PyPI index. Review
   version, source, marker and hash changes. Do not hand-edit hashes to bypass a
   failed download or verification.
3. Re-run the selected profile with `uv sync --locked`, all checks above and the
   CI matrix. Preserve optional-dependency isolation.
4. Commit the reviewed lock and manifest changes together. Never resolve updates
   automatically as part of an ordinary test or service-start command.

## Remaining hardening and release work

No external release or publication is authorized by these checks. Before release:

- Complete pinned PX4 SITL telemetry, takeover and native-failsafe acceptance;
  live active command translation is still absent.
- Implement a real second adapter only when its concrete protocol and lifecycle
  requirements are defined; exercise shared contracts against both firmware
  configurations. There is no ArduPilot adapter in this repository.
- Validate deployment limits on a selected companion computer/OS, recording
  firmware, source IDs, timing assumptions, shutdown and intervention behavior.
- Obtain the owner's license choice, approve distribution name/metadata and
  release/version policy, and confirm packaging and hosted CI for that exact ref.
- Publish only through a separately authorized release task. No upload action,
  registry credentials or automatic version/tag creation is configured here.

These are explicit outstanding acceptance items. The hardening slice does not
establish flight readiness or full Stage 6 completion.
