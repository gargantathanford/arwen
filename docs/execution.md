# Stage 4 execution core

The execution core is implemented and validated with a scripted in-memory vehicle.
It coordinates a single local-position request through Stage 3 admission, final
checks, submission, acknowledgement and observation-based completion. It is not
live PX4 active control: no MAVLink commands, arming, mode changes or setpoint
streams have been added. PX4 remains read-only. Stage 2 live SITL and Stage 3
firmware authority/failsafe acceptance remain pending.

## Run the next working example

From the installed development checkout:

```bash
source .venv/bin/activate
python -m arwen.applications.execution_demo
python -m pytest
```

Expected example output starts with:

```text
submitted
acknowledged
completed
```

The application explicitly injects a synthetic acknowledgement, then a new
synthetic position observation. Submission does not move the vehicle or invent
an acknowledgement. This is neither a physics simulation nor a PX4 integration
test. It sends no network traffic.

## Ownership and API

Applications compose `ExecutionController`, an `AuthorityController`, an
`ExecutionAdapter`, an `ExecutionConfig` and the same monotonic clock. All calls
must be serialized by one application owner; adapter methods must be nonblocking
and must not reenter control. Evidence producers must not refresh cached times.

1. Supply real, fresh normalized state/evidence to the authority controller and
   explicitly acquire a grant as described in [safety-authority.md](safety-authority.md).
2. `start(request, evidence)` takes the adapter snapshot, refreshes authority,
   queues/releases the request and checks adapter capability. The exact released
   request is revalidated immediately before synchronous submission. A busy,
   closed, faulted or history-saturated controller rejects new dispatches.
3. `update(evidence)` rechecks authority and deadlines before interpreting bounded
   feedback. The application must drive it regularly. It never retries or resends.
4. `cancel()` stops future local output for the active request and ends tracking.
   `close()` stops active work and permanently closes the authority controller;
   call it before closing the adapter/transport. Both are idempotent once inactive.
5. `outcome(id)` reads a recorded outcome, not current permission or health.
   History is bounded and never evicts replay protection. Creating a new controller
   is an explicit lifecycle change, not an automatic way to retry old work.

`ExecutionAdapter` extends the unchanged Stage 1 `VehicleAdapter` with a single
nonblocking `acknowledgement()` read and `stop(request_id)`. A STOPPED result means
that request will produce no future local output. It does not claim that the
controller stopped moving, that a remote cancellation was acknowledged or that
an earlier physical effect was recalled. In-flight physical cancellation needs
firmware-specific semantics before a live adapter is implemented.

## Outcomes and failure behavior

- SUBMITTED means the adapter accepted a local submission, not that firmware did.
- ACKNOWLEDGED requires an explicit accepted event matching request, source and
  estimator origin. Old, future and unrelated events cannot advance execution.
- COMPLETED also requires fresh post-submission telemetry in the original frame,
  within the configured Euclidean position tolerance in metres. Cached target
  coordinates, including a position already at the target, are insufficient.
- Controller rejection, ACK timeout and observed-completion timeout terminate
  tracking and stop local output. All time limits are exclusive. Request and grant
  expiry can interrupt earlier than the execution deadlines.
- Takeover, failsafe, disconnect, unknown/stale evidence, stale/missing position,
  source/frame changes or revoked authority interrupt execution before success.
  Fresh telemetry never silently revives an old grant or restarts a request.
- Adapter exceptions, mismatched submission correlation and unconfirmed/failed
  stops latch a fault, revoke authority and prevent further dispatches. A send
  exception can mean uncertain delivery; it is never retried automatically.
- Invalid/regressing monotonic time terminates tracking. There are no background
  timers: the owner must keep calling update; close always attempts local stop.

Configuration selects ACK/completion durations and position tolerance explicitly.
These are workflow limits, not universal vehicle safety limits. Resource bounds
limit history and feedback processed per update. Local cancellation does not
retract physical effects, and a local final check is not atomic with remote
firmware. The adapter must independently enforce its native constraints.

## Remaining live work

The firmware-neutral extension and local execution engine are implemented; the
following are still required before live Stage 4 acceptance:

- Run the pinned Stage 2 PX4 v1.16.0 SITL smoke.
- Establish PX4-specific permission/takeover/failsafe observations and validate
  authority transfer, stale-state behavior and native failsafes.
- Implement a scoped live adapter with supported command translation, source and
  request correlation, native limits, streamed-output shutdown where applicable,
  firmware acknowledgements and observation-based outcomes.
- Exercise success, rejection, timeouts, interruption and cancellation in SITL
  before any aircraft operation. Confirm no queued work reasserts autonomy.

No live simulator or hardware test was run for this change. The Debian cloud
machine has no PX4/Gazebo installation. Tests and the scripted application do not
establish live flight behavior or flight readiness.
