# ADR 0004: Local authority leases and cancellation

Status: accepted for Stage 3 local policy; live firmware acceptance pending.

Preserve Stage 1–2 APIs and the TIMESYNC-only wire allowlist. Add immutable,
firmware-neutral `AuthorityEvidence` in vehicle: source, host monotonic observation
time, explicit link health and authority mode (unknown, autonomy permitted,
operator, failsafe). A heartbeat, connection, armed bit or request authority string
cannot establish autonomy permission. The current PX4 monitor cannot supply this
evidence; missing evidence is unknown. Later firmware integration must establish
manual takeover and failsafe semantics with simulator contract tests.

`safety` evaluates evidence and position freshness plus request time/frame policy.
`control` owns authority grants, request identity and pending work, using only
vehicle/config/safety imports. No new import edges or dependencies are needed.
Applications serialize all calls with one injected host monotonic clock and supply
trusted, normalized evidence; this is an in-process ownership mechanism, not
user authentication or protection against malicious Python callers.

A grant has a new opaque ID, owner, source, estimator origin, issue time and an
exclusive expiry. There is at most one grant and no implicit renewal, replacement
or transfer. Revocation or expiry cancels all pending work. Operator takeover,
failsafe, disconnect, missing/stale evidence, missing/stale position, a source or
estimator-session change, and regressing timestamps also revoke. Recovery never
restores the old grant. New grants require explicit issuance and an authority
observation strictly newer than the last revocation; a recorded takeover must be
followed by a fresh explicit autonomy-permitted observation. An active grant
cannot be replaced without revocation. Ordinary explicit revocation also imposes
this fresh-evidence requirement.

Queueing checks authority and safety. Releasing a pending request rechecks both
against the latest evidence and clock; it consumes the request once. Expiry and
freshness use exclusive limits. Requested lifetime is bounded at queue and release.
Grant duration is bounded. A forged ID cannot confer permission. IDs are retained
for the controller lifetime including denied requests; replay history and queue
are bounded. When the ID budget is exhausted, new IDs fail closed permanently
for this controller, rather than evicting old replay protection. Capacity rejection
is terminal for that ID. Result batches from update/revoke/close report cancelled
pending IDs; callers own diagnostic persistence. `decision(id)` retains the last recorded
outcome per tracked ID, within the same replay budget, including cancellation
triggered by a later grant/queue/release call. It is not a health check or permission. Cancellation does not retract an
already released request or assert a physical effect.

Invalid/nonmonotonic host time revokes and cancels, then raises ValueError. Closed
controllers cannot resume. Safety evidence may only advance in observation and
measurement/receipt time for the same source/session; older input revokes rather
than rolling back a takeover. No timer/thread exists: update, grant, queue and
release enforce current conditions. Applications must drive update regularly.

There is deliberately no call to VehicleAdapter.submit, repeated command output,
firmware mode change or production dispatch path. A release decision is a local
policy outcome, never a reusable flight permission. Stage 4 must atomically
recheck at the send boundary and define cancellation of in-flight/repeated work,
controller acknowledgement and observed completion. Companion policy cannot
replace controller failsafes or pilot intervention. Native failsafe effectiveness
and live takeover have not been demonstrated by deterministic tests.
