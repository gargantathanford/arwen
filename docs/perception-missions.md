# Stage 5: observed-target mission

The first Stage 5 slice adds a typed observation, deterministic mission policy and
application composition. The use case is one position request at a selected target
already localized in the vehicle's local north/east/down estimator frame. It is a
bounded workflow example, not a universal aircraft mission or a path planner.
There is no camera/detector implementation, live perception model or firmware
command integration. PX4 remains passive.

## Run the complete scripted workflow

From the installed development checkout:

```bash
source .venv/bin/activate
python -m arwen.applications.mission_demo
python -m pytest
```

Expected output begins:

```text
executing
succeeded
execution_completed
```

The example explicitly injects synthetic target observations, an acknowledgement
and a new vehicle position. Its success is neither physical flight nor a physics
or PX4 simulation. It sends no network traffic and loads no perception model.

## Observation semantics

`perception.observations.TargetObservation` carries selected sensor/target IDs,
measurement and receipt times, optional `LocalPosition`, a finite [0,1] confidence
score and nonnegative uncertainty in metres. All times use the same injected host
monotonic domain as vehicle state. Receipt never refreshes measurement age; duplicate
measurements can become stale even when newly received.

A producer must document confidence calibration and the meaning of its conservative
uncertainty estimate. These fields are not inferred statistical guarantees. Missing
position is unknown; valid zero coordinates remain valid. The observation must
already be transformed into the correct estimator origin by a future producer.
Matching origin names alone are not proof of calibration. No body/camera/global
frame transformation, estimator-reset detection or detector is added here.

## Mission policy

`ObservedTargetMission` imports configuration, perception and vehicle contracts.
It emits `PositionIntent`, with no adapter, authority ID or firmware SDK. The
application routes intent; the mission cannot send commands or acquire authority.
`ObservedTargetConfig` explicitly selects mission/vehicle/sensor/target identities,
state and observation age limits, confidence floor, uncertainty ceiling, allowable
target displacement and intent lifetime. These are application choices.

- WAITING: missing, stale or unsuitable data produces no intent. Qualifying data
  can recover this initial state without restarting the object.
- READY: a qualifying target creates one unique, immutable intent. The target is
  frozen. Its exclusive admission deadline is capped by intent lifetime and both
  current vehicle/observation freshness horizons.
- EXECUTING: the application has dispatched the current intent. Subsequent selected
  observations must remain fresh and qualifying. Target position may vary within
  the configured displacement bound but does not retarget the request.
- SUCCEEDED: the application reports matched execution completion, which requires
  an accepted acknowledgement and fresh post-submission vehicle telemetry.
- ABORTED: unavailable sensors, poor quality, target movement, stale/regressing or
  contradictory samples, source/frame change, cancellation or execution failure.

After selection, invalid data aborts rather than waits or silently replans. A new
fresh sample after a gap cannot bridge expired ongoing evidence. A contradictory
payload at the same measurement time aborts. Terminal states are sticky: new data,
late feedback or repeated close cannot restart a mission. There are no automatic
retries, new grants, reacquisition, landing or recovery actions.

`evaluate(state, observation, now)` advances policy. `dispatched(intent_id)` and
`finished(intent_id, succeeded)` are trusted application callbacks with exact intent
correlation; they do not independently establish physical effects. The mission
policy cannot validate a caller's claim that a command completed.

## Application composition and deadlines

`applications.observed_target.ObservedTargetApplication` owns one mission, an
explicitly issued Stage 3 grant, a Stage 4 execution controller and their lifecycle.
Drive `tick(selected_observation, authority_evidence)` regularly from one serialized
owner. Supply None when the selected sensor is unavailable. This API is not a
multi-target event bus; unrelated observations must be filtered by the producer.
Unexpected sensor/target identity is treated as invalid selected-stream evidence.

The application creates a correlated `PositionRequest` from the intent and existing
grant. It never acquires or renews authority automatically. `ExecutionController.start`
now accepts optional `admit_before=` and rechecks it immediately before submission.
Existing callers are unchanged. An expired intent cannot dispatch merely because
a newly constructed command has a longer expiry.

Admission deadline and command lifetime are distinct. Once execution starts,
request/lease/deadline checks remain enforced by Stage 4 while Stage 5 requires
ongoing fresh observations. The application-selected request lifetime must fit
the Stage 3 policy's TTL bound. Perception quality never confers authority or
bypasses adapter capabilities, source/frame checks or controller acknowledgement.

On termination, the application closes execution, stopping future local output and
invalidating its grant. `close()` cancels active work and is idempotent. A stop cannot
recall an earlier physical effect. Invalid clock or adapter errors abort and attempt
cleanup; the returned snapshot includes diagnostic detail.

## Validation and limitations

Deterministic tests cover the complete synthetic workflow, waiting/recovery,
measurement freshness, exact limits, confidence and uncertainty, unknown position,
source/frame selection, duplicate/reordered/conflicting samples, target movement,
intent expiry at submission, takeover/failsafe/disconnect, execution rejection,
cancellation and terminal replay prevention. Existing Stage 1–4 tests remain in
place, including the outbound TIMESYNC-only allowlist.

Live PX4 SITL, real sensor inference/calibration and live firmware authority/failsafe
acceptance have not been run. Stage 2–4 live acceptance remains pending; this Stage 5
slice does not bypass those prerequisites. See [px4-sitl.md](px4-sitl.md),
[safety-authority.md](safety-authority.md) and [execution.md](execution.md).

The roadmap's next stage is platform hardening: reproducible tools, CI and eventual
second-adapter validation. No ArduPilot support or release readiness is claimed.
