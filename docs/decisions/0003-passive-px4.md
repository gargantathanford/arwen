# ADR 0003: Passive PX4 integration

Status: accepted for Stage 2.

Keep Stage 1 vehicle APIs unchanged. UDP transport owns a single explicit socket;
its owner calls synchronous update, with bounded poll duration and message budget.
Transport decoding yields communication-owned records. PX4 interprets those
records only after selecting an expected system/component PX4 non-GCS heartbeat.
The UDP peer is pinned at that heartbeat. IDs are routing filters, not security.

Use optional pymavlink >=2.4.49,<2.5 (Python 3.12 wheels). Lazy dialect loading and
all third-party typing gaps are confined to communication/mavlink.py. Core imports
have no optional dependency or runtime side effects.

Only TIMESYNC requests are transmitted, from system 255/component 191; no
heartbeat or response or command is emitted. A correlated response from the
selected source bounds host-minus-PX4 boot-clock offset between send-remote and
receive-remote. Use the lower bound (with 1 ms quantization allowance and 100 ppm
relative drift allowance) so measurement age is conservative rather than optimistic.
Reject RTT above the configured bound. Alignment expires and is invalidated on
loss, reconnection, or detected boot-clock discontinuity. This assumes PX4's
TIMESYNC clock and LOCAL_POSITION_NED clock share boot origin and run at real-time
speed; accelerated simulation is unsupported. Clock quality is observable.

Snapshots are absent until a selected heartbeat; position stays None until
alignment and a valid measurement exist. Heartbeats never alter position time.
Duplicate/reordered boot samples are ignored. 32-bit wrap is unwrapped relative
to synchronized boot time. A backwards TIMESYNC boot clock or offset interval
inconsistent with drift bounds indicates a restart/clock discontinuity: start a
new frame/session and invalidate cached position. Small reordered samples alone
cannot distinguish reboot from delay. Detection requires TIMESYNC or link loss.
LOCAL_POSITION_NED cannot report estimator origin resets; undetected resets are
an explicit limitation, not equivalence with an application origin.

Disconnect closes the socket and invalidates cached state. Reconnection is
explicit close/open, never an automatic retry. Health inspection checks timeout
without requiring traffic. Receive errors transition to failed and clean up.
There is no production safety policy or authority implementation. The next stage
is safety and authority before active control.
