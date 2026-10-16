# Stage 3: local safety and authority

Stage 3 implements local authority grants, revocation, operator takeover handling,
cancellation, stale-request rejection and bounded replay protection. It does not
send commands or change PX4 modes. Stage 1 APIs and Stage 2's passive adapter and
TIMESYNC-only outbound allowlist remain unchanged.

## Run a deterministic scenario

From the installed development checkout:

```bash
source .venv/bin/activate
python -m arwen.applications.authority_demo
python -m pytest
```

The scenario uses synthetic state and authority evidence. It queues one local
request, observes operator takeover, cancels it and demonstrates failed release.
It does not connect to a simulator or aircraft.

## Application composition

Applications create `AuthorityController` with `SafetyConfig` and an injected
host monotonic clock. All calls have one serialized owner. The configuration
requires explicit position freshness, request lifetime, authority freshness and
grant lifetime bounds. There is no universal aircraft safety threshold.

1. Call `update(state, evidence)` with immutable normalized evidence. Call this
   regularly even without new input, passing cached records without changing
   their timestamps. Missing evidence stays unknown. Applications persist the
   returned cancellation decisions for diagnostics.
2. When the application explicitly intends to acquire authority, call
   `grant(owner_id, lifetime_s)`. A valid grant requires a known fresh position,
   healthy link and fresh explicit autonomy-permitted evidence for that source.
   It is pinned to the vehicle source and estimator-session origin. A currently
   active grant cannot be replaced or extended implicitly.
3. Construct `PositionRequest` with the returned opaque grant ID and unique
   request ID. `queue(request)` checks grant, request lifetime, current position,
   authority evidence and frame. The request must not predate the grant.
4. `release(id)` rechecks against the current clock and cached evidence and
   consumes pending work once. `authorized_locally` is a policy outcome only:
   no adapter is called, and it does not mean firmware support, acknowledgement,
   physical completion or permission to send at a later time.
5. `cancel(id)` cancels a pending request. `revoke()` cancels every pending
   request and invalidates the grant. `close()` does the same and permanently
   closes this controller. Repeated cancellation returns unknown-request;
   repeated close is harmless. Already released work cannot be recalled here.

`decision(id)` returns the last recorded outcome for a tracked ID, including
revocation detected by queue/release/grant. It does not reevaluate freshness or
confer permission. Live conditions are enforced by update/queue/release/grant.

## Failure and recovery rules

- Operator takeover, failsafe, disconnect, unknown/stale evidence and
  missing/stale position revoke authority and clear pending requests.
- Vehicle source or estimator-origin changes revoke the old grant.
- Request expiry and freshness limits are exclusive: equality is stale/expired.
  Expired pending requests are removed on update or denied at release.
- A long gap between updates revokes the old grant even if the next update
  contains fresh data. Recovery must not bridge an unobserved stale interval.
- Revocation never automatically reacquires authority. The application must
  supply an autonomy-permitted observation strictly newer than revocation and
  explicitly request a new grant with a new ID. Old queued work stays cancelled.
- Regressing evidence timestamps revoke instead of restoring old observations.
  The same applies to measurement/receipt timestamps within one estimator frame.
- Invalid or backwards host time revokes and cancels, then raises `ValueError`.
  Do not catch this error and assume the old grant survived.
- Request IDs, including denied requests, remain reserved for the controller's
  lifetime. Queue capacity rejection is terminal for that ID. The finite ID
  budget fails closed permanently when full; no replay records are evicted.
  Recreating the controller is an explicit application lifecycle change, not
  automatic recovery. Do not replay old work into a new controller.

This is a trusted in-process ownership model, not authentication of arbitrary
callers. A matching source or grant ID string does not establish real operator
permission. No background worker monitors inputs; applications must drive updates.
Policy checks temporal/source/frame readiness, not obstacles, geofences,
attitude/energy margins or full navigational validity.

## PX4 and remaining acceptance work

`AuthorityEvidence` is a firmware-neutral addition in `vehicle.authority`.
It contains the source, observation timestamp, explicit link health and one of
UNKNOWN, AUTONOMY_PERMITTED, OPERATOR or FAILSAFE. A producer must actually
establish new evidence before advancing its timestamp.

The current PX4 HEARTBEAT/LOCAL_POSITION_NED monitor does **not** establish these
authority semantics. No new mode decoder or optimistic heartbeat-to-permission
mapping has been added. Composing it without explicit authority evidence fails
closed. It still advertises no position-request capability and always returns
`UNSUPPORTED` from `submit()`.

Deterministic tests establish local cancellation and denial behavior. Live PX4
SITL operator takeover and native failsafe effectiveness have **not been run or
established**. PX4/Gazebo are absent from the Debian cloud environment. The pinned
PX4 v1.16.0 setup and outstanding Stage 2 smoke remain in [px4-sitl.md](px4-sitl.md).
Before live acceptance, define and validate PX4-specific operator/failsafe
observations and exercise loss, takeover, re-acquisition and session restart in
the pinned simulator. Do not treat unit tests as proof of firmware behavior.

The Stage 4 [execution core](execution.md) now implements final local checks,
stop handling, acknowledgement correlation and observed outcomes with a scripted
adapter. Live active control still requires those acceptance checks, scoped
command translation and firmware-specific cancellation behavior. Native controller failsafes,
RC/manual intervention and the external GCS remain independent. This local layer
does not replace them and is not flight-ready.
