# ADR 0005: Single-owner execution lifecycle before live dispatch

Status: accepted for the Stage 4 execution core. Live PX4 dispatch remains gated
by Stage 2–3 simulator acceptance; no MAVLink command allowlist is expanded.

Introduce an optional ExecutionAdapter protocol extending VehicleAdapter with
correlated acknowledgements and explicit stopping of future local output.
Submission acceptance, firmware acknowledgement and observed target arrival are
three separate outcomes. A stop confirmation means no more local emissions for
that request, never that previous physical effects were undone.

Control owns one active position request at a time. Start queues/releases through
the existing authority controller, checks adapter capability and revalidates the
exact released request immediately before synchronous submission. One serialized
application owner drives update; callbacks, re-entrancy and concurrent calls are
unsupported. This is a local final check, not an atomic guarantee with remote
firmware. There are no automatic retries, repeated setpoint streams or async tasks.

Every update refreshes evidence and revalidates the original grant/session before
processing feedback or declaring success. Revocation, takeover, link loss, stale
state/evidence, session changes and request expiry stop local output and terminate
tracking. Invalid clock input also stops tracking and latches a fault. An adapter
exception, mismatched submission correlation or unconfirmed stop latches a fault
and blocks subsequent dispatches. Application shutdown must stop tracking before
closing transport. Repeated close/stop is harmless at the coordinator boundary.

An acknowledgement must match request/source/origin, occur no earlier than
submission and no later than the host clock, and arrive before the exclusive ACK
deadline. Completion requires an accepted acknowledgement and a fresh state in
the original frame, measured strictly after submission, within the configured
Euclidean tolerance in metres. ACK and completion deadlines and the request/lease
lifetimes are independent exclusive limits. Old telemetry or a submission result
cannot manufacture completion. Feedback is bounded per update; request history
is bounded and fails closed rather than evicting replay records.

The authority controller retains exact released requests (bounded by its existing
ID budget) for revalidation. This does not change release's one-shot behavior.
Existing Stage 1–3 methods and passive PX4 behavior are preserved. The synthetic
application is a scripted in-memory vehicle, not a PX4 or physics simulator.
Live command translation, mode transitions, streamed-setpoint cancellation and
firmware acknowledgement correlation remain unimplemented until live authority
and failsafe acceptance is established. No readiness claim follows from this core.
