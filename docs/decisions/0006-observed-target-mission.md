# ADR 0006: One observed-target mission

Status: accepted for the Stage 5 synthetic-observation slice; no live flight claim.

Use one concrete policy: request a position at a selected observed target already
localized in the vehicle's estimator NED frame. Perception owns immutable target
observations with sensor/target identity, measurement and receipt times, confidence
and positional uncertainty in metres. There is no camera, detector, SDK or implicit
coordinate transform. Zero coordinates are valid; missing position is unknown.

Missions owns mission intent and a deterministic one-shot state machine, importing
only config, perception and vehicle contracts. Applications translates intent to a
PositionRequest and composes the existing authority/execution layers. Control never
imports missions or perception; no internal dependency rule is expanded.

Before selection, unavailable or unsuitable data waits. After selecting a target,
missing/stale/low-quality evidence, wrong identity/frame/source, regressing samples
or excessive target displacement abort instead of silently retargeting. The target
is frozen once selected. New matching observations must continue to support it
through execution. Duplicate measurements do not refresh measurement time. A
contradictory payload at the same measurement timestamp aborts. Explicit application
thresholds determine admissible confidence, uncertainty, age and target motion.

Intent has a unique ID and exclusive admission deadline capped by current state
and observation freshness plus configured intent lifetime. Its deadline controls
when dispatch may start, not how long execution may continue. Execution.start gains
an optional admission deadline checked immediately before submission; existing
callers retain their behavior. Requests have a separate bounded lifetime, while
subsequent mission ticks require fresh observations, authority and vehicle state.

The application owns one grant and one execution lifecycle. It never acquires,
renews or recovers authority automatically. It aborts on execution failure,
operator takeover, sensor loss or invalid clock input and closes execution to stop
future local output and invalidate the grant. Success requires the execution
layer's acknowledged, fresh post-submission target observation. Terminal mission
states are sticky, and old intent/feedback cannot restart a mission.

The runnable scenario injects synthetic observations and uses the existing scripted
vehicle. No perception algorithm, obstacle avoidance, path planner or live command
translation is added. PX4 remains passive. Stage 2–4 live acceptance and Stage 6
hardening remain separate work; a synthetic scenario does not establish either.
