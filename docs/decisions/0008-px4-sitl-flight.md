# ADR 0008: Opt-in PX4 1.16.0 / SIH end-to-end flight

Status: implemented, acceptance evidence is recorded in the SITL runbook.

Preserve the passive monitor and its TIMESYNC-only codec. Add a distinct flight
codec/transport and Px4SitlAdapter for one explicitly selected local PX4 v1.16.0
SIH quadrotor (airframe 10040). New decoded records remain inside communication.
A bounded receive drain now skips unknown datagrams without starving telemetry.
No generic adapter, mission policy or synthetic backend is treated as live proof.

Reuse Stage 2 source/peer selection, UDP ownership, clock/snapshot interfaces,
Stage 3 authority leases and request admission, and Stage 6 locked tooling. Add
current_grant() to check a lease for command/mode phases. Do not force streamed
setpoints through ExecutionController's command-acknowledgement model: that model
requires explicit ACKs, while SET_POSITION_TARGET_LOCAL_NED has none. The new
scenario tracks real command transactions and position-stream arrival separately;
the generic submit() remains deliberately unsupported on this adapter.

COMMAND_LONG output is narrowly restricted to one version request (512), one
Offboard mode request (176), or an explicitly selected unsupported-mode negative probe, one normal non-forced arm (400) and one land-at-current-
position request (21), with no automatic retries or forced disarm. Commands are
serialized and IDs cannot be reused within a session because MAVLink COMMAND_ACK
has no arbitrary request UUID. ACK source/target, command ID, receive time and a
fixed deadline must match. Accepted ACK does not establish the observed mode,
armed state, landed state or physical arrival. IN_PROGRESS does not extend the
deadline. Position streams use frame LOCAL_NED, position-only mask 3576, a boot-
aligned uint32 timestamp and 20 Hz output; selected PX4 stamps reception locally.

Explicit --authorize-sitl-flight is the operator action. Default invocation only
validates telemetry and parameters; it never requests a mode, arms or flies.
Exclusive UDP binding and an OS vehicle lock prevent another local ARWEN flight
owner. The initial observed mode must not already be Offboard, and the vehicle
must be landed/disarmed. This is a trusted local simulation profile, not network
identity authentication or proof that another application cannot send MAVLink.

Fresh heartbeat/mode, extended landed state, aligned position, estimator status
and NED odometry/reset evidence are mandatory throughout control. Targets and
measured streaming positions are bounded relative to the starting estimator
position. Arrival requires distinct fresh measurements continuously within XY/Z
tolerances for a dwell interval. Landed and disarmed are separately observed.
Expected mode transitions are explicit; an unexpected mode revokes authority.
No observer reconnection can reacquire the grant or rearm.

SIH at its default unaccelerated factor advances a discrete 4 ms simulated clock
which can pause/slow relative to host time. Its policy is therefore distinct from
the passive monitor's oscillator model: retain bounded causal TIMESYNC anchors,
use the latest anchor preceding a sample, and subtract a 5 ms clock/quantization
allowance. Delayed measurements never use a newer offset that could make them
appear fresh. Unknown pre-anchor samples are unavailable. Remote regression or
clock acceleration outside the interval/tick bound invalidates the session.
Wall-clock changes are irrelevant. Packet RTT remains bounded and sample age is
conservative; excessive slowdown/stalls cause stale-data failure. Accelerated
simulation is unsupported and the harness removes the speed-factor override.

ODOMETRY reset_counter sums position, velocity and attitude reset counters in the
selected PX4; an undifferentiated change is ambiguous and aborts. The selected
SIH profile uses EKF2_MAG_TYPE=6 (magnetometer initialization only), avoiding the
normal post-takeoff heading realignment of continuous magnetometer fusion. The
real ULog identified that heading reset; changing from automatic to heading-only
fusion did not remove it. The parameter is
read back; resets during authorization are never ignored to manufacture success.
Resets during unauthorised preflight invalidate its position/frame and restart
the required stable-observation interval.

On takeover, stale telemetry, failed command/mode, reset, expired authority or
scheduler gap, stop future setpoints and all flight commands. Do not issue an
emergency mode change that could override the operator. PX4 is configured and
read back with Offboard-loss timeout 0.5 s, Land action and auto-disarm after
landing; its actual response must be observed. Stopping output is never physical
stop confirmation. Bounded observation-only reconnection is allowed after abort,
without mode/arming commands. Full A–D tests are opt-in and record real wire and
application logs; default tests cannot claim simulator acceptance.
