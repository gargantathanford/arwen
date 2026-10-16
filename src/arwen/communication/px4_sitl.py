"""Opt-in PX4 1.16.0 SIH flight surface. Passive Px4Adapter is unchanged.

Position setpoints never create COMMAND_ACK events. Only the finite commands
below use transactions; no retries, parameter writes or forced arm/disarm exist.
"""

from collections.abc import Callable
from dataclasses import dataclass
from math import hypot, isclose, isfinite
from uuid import uuid4

from arwen.communication.flight_messages import (
    CommandAck,
    Estimator,
    FlightHeartbeat,
    LandedState,
    Odometry,
    Parameter,
    Version,
)
from arwen.communication.mavlink import FlightUdpTransport
from arwen.communication.messages import Packet, Position, Timesync
from arwen.communication.px4 import ConnectionState, Px4Adapter, TimingQuality
from arwen.config.sitl import SitlConfig
from arwen.config.telemetry import TelemetryConfig
from arwen.vehicle.contracts import LocalPosition, VehicleState

# Values are required/read back, never written by ARWEN.
REQUIRED_PARAMETERS: dict[str, float] = {
    "SYS_AUTOSTART": 10040,
    "EKF2_MAG_TYPE": 6,
    "COM_RC_IN_MODE": 4,
    "COM_OF_LOSS_T": 0.5,
    "COM_OBL_RC_ACT": 4,
    "COM_DISARM_LAND": 1,
}


@dataclass(slots=True)
class CommandTransaction:
    request_id: str
    command: int
    sent_at: float
    deadline: float
    result: int | None = None
    acknowledged_at: float | None = None


class Px4SitlAdapter(Px4Adapter):
    def __init__(
        self,
        wire: FlightUdpTransport,
        telemetry: TelemetryConfig,
        limits: SitlConfig,
        clock: Callable[[], float],
        emit: Callable[[str, dict[str, object]], None],
    ) -> None:
        super().__init__(wire, telemetry, clock)
        self._wire = wire
        self.limits = limits
        self._emit = emit
        self.heartbeat: FlightHeartbeat | None = None
        self.heartbeat_at = 0.0
        self.landed: int | None = None
        self.landed_at = 0.0
        self.version: Version | None = None
        self.parameters: dict[str, float] = {}
        self._odometry: Odometry | None = None
        self._estimator: Estimator | None = None
        self._last_read = -float("inf")
        self.transaction: CommandTransaction | None = None
        self._used_commands: set[int] = set()
        self._authorization: str | None = None
        self._authorization_deadline = 0.0
        self._home: LocalPosition | None = None
        self.target: LocalPosition | None = None
        self._stream_request_id: str | None = None
        self.fault: str | None = None
        self._last_stream: float | None = None
        self._clock_anchors: list[tuple[float, float]] = []

    def _new_session(self) -> None:
        if self._authorization is not None:
            self.abort("source/session clock changed")
        super()._new_session()
        self.heartbeat = None
        self.landed = None
        self.version = None
        self.parameters.clear()
        self._odometry = None
        self._estimator = None
        self.transaction = None
        self._used_commands.clear()
        self._last_read = -float("inf")
        self._clock_anchors.clear()

    def _consume(self, packet: Packet, now: float) -> None:
        super()._consume(packet, now)
        if (
            (packet.system, packet.component)
            != (self._config.system_id, self._config.component_id)
            or packet.peer != self._peer
            or self._status is not ConnectionState.HEALTHY
        ):
            return
        payload = packet.payload
        if isinstance(payload, FlightHeartbeat):
            if payload.autopilot != 12 or payload.vehicle_type != 2:
                return
            if self.heartbeat != payload:
                self._emit(
                    "vehicle_status",
                    {
                        "mode": list(payload.mode),
                        "armed": payload.armed,
                        "system_status": payload.system_status,
                    },
                )
            self.heartbeat, self.heartbeat_at = payload, now
        elif isinstance(payload, LandedState):
            self.landed, self.landed_at = payload.state, now
        elif isinstance(payload, Version):
            self.version = payload
        elif isinstance(payload, Parameter):
            if payload.name in REQUIRED_PARAMETERS and isfinite(payload.value):
                self.parameters[payload.name] = payload.value
        elif isinstance(payload, Odometry):
            if self._odometry is not None and payload.boot_us <= self._odometry.boot_us:
                return
            if (
                self._odometry is not None
                and payload.reset_counter != self._odometry.reset_counter
            ):
                if self._authorization is not None:
                    self.abort("estimator reset counter changed")
                else:
                    self._session = uuid4().hex
                    self._state = None
                    self._last_boot_s = None
                    self._emit(
                        "preflight_estimator_reset",
                        {"reset_counter": payload.reset_counter},
                    )
            self._odometry = payload
        elif isinstance(payload, Estimator):
            if self._estimator is None or payload.boot_us > self._estimator.boot_us:
                self._estimator = payload
        elif isinstance(payload, CommandAck):
            pending = self.transaction
            if (
                pending is None
                or pending.result is not None
                or payload.command != pending.command
                or (payload.target_system, payload.target_component) != (255, 191)
                or now >= pending.deadline
            ):
                return
            self._emit(
                "command_ack",
                {
                    "request_id": pending.request_id,
                    "command": payload.command,
                    "result": payload.result,
                },
            )
            if payload.result != 5:  # IN_PROGRESS is not completion, deadline is fixed.
                pending.result, pending.acknowledged_at = payload.result, now

    def poll(self) -> None:
        super().update()
        now = self._now()
        if self.connection_state is not ConnectionState.HEALTHY:
            return
        if self.version is None and 512 not in self._used_commands:
            self._command(
                512, (148, 0, 0, 0, 0, 0, 0), "request-version", read_only=True
            )
        if now - self._last_read >= 2:
            for name in REQUIRED_PARAMETERS:
                self._wire.parameter_read(name)
            self._last_read = now
        pending = self.transaction
        if pending is not None:
            if pending.result is None and now >= pending.deadline:
                self.abort(
                    f"command {pending.command} acknowledgement timed out; delivery is uncertain"
                )
                raise RuntimeError(self.fault)
            if pending.result not in (None, 0):
                self.abort(
                    f"command {pending.command} rejected with result {pending.result}"
                )
                raise RuntimeError(self.fault)

    def _synchronize(self, message: Timesync, now: float) -> None:
        # SIH at factor 1 advances simulated time no faster than host time. Allow
        # can pause or slow, not a generic oscillator or accelerated-SITL policy.
        if self._pending is None or message.echo_ns != self._pending[0]:
            return
        sent = self._pending[1]
        self._pending = None
        if message.remote_ns <= 0 or not 0 <= now - sent <= self._config.sync_max_rtt_s:
            return
        remote = message.remote_ns / 1e9
        low, high = sent - remote, now - remote
        if self._offset is not None:
            if remote < self._remote_s or high < self._offset[0] - 0.005:
                self._new_session()
        self._offset = (low, high)
        self._remote_s, self._aligned_at = remote, now
        self._clock_anchors.append((remote, low))
        self._clock_anchors = self._clock_anchors[-64:]

    def _measurement_time(self, boot_s: float) -> float | None:
        # A causal older anchor bounds a later measurement for SIH factor 1,
        # whose clock can pause/slow but cannot intentionally run faster than
        # wall time. Never map a delayed sample using a newer offset. One SIH
        # tick (4 ms) plus 1 ms quantization is retained as uncertainty.
        for remote, low in reversed(self._clock_anchors):
            if remote <= boot_s:
                return boot_s + low - 0.005
        return None

    def _position(self, message: Position, now: float) -> None:
        if (
            not 0 <= message.boot_ms < 2**32
            or not all(isfinite(v) for v in (message.north, message.east, message.down))
            or self.timing_quality is not TimingQuality.BOUNDED
        ):
            return
        wrap = 2**32 / 1000
        raw = message.boot_ms / 1000
        rtt = self._offset[1] - self._offset[0] if self._offset is not None else 0.0
        expected = self._remote_s + now - self._aligned_at + rtt
        boot = raw + round((expected - raw) / wrap) * wrap
        if (
            boot < 0
            or boot > expected + 0.003
            or self._last_boot_s is not None
            and boot <= self._last_boot_s
        ):
            return
        measured = self._measurement_time(boot)
        if measured is None or not 0 <= measured <= now:
            return
        self._last_boot_s = boot
        self._state = VehicleState(
            self._source_id,
            measured,
            now,
            LocalPosition(
                message.north,
                message.east,
                message.down,
                f"{self._source_id}:estimator:{self._session}",
            ),
        )

    def _boot_fresh(self, boot_us: int, now: float) -> bool:
        if self._offset is None or boot_us <= 0:
            return False
        measured = self._measurement_time(boot_us / 1e6)
        return (
            measured is not None and 0 <= now - measured < self.limits.telemetry_age_s
        )

    def readiness(self) -> str | None:
        now = self._now()
        if self.fault:
            return self.fault
        if self.connection_state is not ConnectionState.HEALTHY:
            return "selected PX4 link is not healthy"
        state = self.snapshot()
        if state is None or not state.position_is_fresh(
            now, self.limits.telemetry_age_s
        ):
            return "missing/stale aligned position"
        if (
            self.heartbeat is None
            or now - self.heartbeat_at >= self.limits.telemetry_age_s
        ):
            return "missing/stale quadrotor heartbeat (configure 5 Hz)"
        if self.heartbeat.system_status in (5, 6, 8):
            return "controller reports critical/emergency/flight termination"
        if self.landed is None or now - self.landed_at >= self.limits.telemetry_age_s:
            return "missing/stale EXTENDED_SYS_STATE"
        if (
            self._odometry is None
            or self._odometry.frame != 1
            or not self._boot_fresh(self._odometry.boot_us, now)
        ):
            return "missing/stale NED odometry reset evidence"
        if (
            self._estimator is None
            or self._estimator.flags & 41 != 41
            or self._estimator.flags & 3072 != 0
            or not self._boot_fresh(self._estimator.boot_us, now)
        ):
            return f"missing/stale estimator evidence flags={self._estimator.flags if self._estimator else None} sample_host={self._measurement_time(self._estimator.boot_us / 1e6) if self._estimator else None} now={now}"
        if self.version is None or self.version.software >> 8 != 0x011000:
            return "firmware must be PX4 1.16.0"
        if not self.version.capabilities & 128:
            return "firmware does not advertise local-position setpoints"
        for name, expected in REQUIRED_PARAMETERS.items():
            if name not in self.parameters or not isclose(
                self.parameters[name], expected, abs_tol=0.001
            ):
                return f"required SITL parameter {name}={expected} not confirmed"
        return None

    def authorize(
        self, authority_id: str, expires_at: float, *, operator_confirmed: bool
    ) -> None:
        reason = self.readiness()
        state, heartbeat = self.snapshot(), self.heartbeat
        if (
            operator_confirmed is not True
            or not authority_id.strip()
            or not isfinite(expires_at)
            or not 0 < expires_at - self._now() <= self.limits.total_timeout_s
            or reason is not None
            or heartbeat is None
            or heartbeat.armed
            or self.landed != 1
            or heartbeat.mode not in {(1, 0), (2, 0), (3, 0), (4, 3), (7, 0)}
            or state is None
            or state.position is None
        ):
            raise RuntimeError(
                f"flight authorization refused: {reason or 'operator/ground/mode condition'}"
            )
        self._authorization, self._authorization_deadline = authority_id, expires_at
        self._home = state.position
        self._emit(
            "operator_authorized",
            {
                "authority_id": authority_id,
                "expires_at": expires_at,
                "source_id": state.source_id,
                "origin_id": state.position.origin_id,
            },
        )

    def _control_ready(self) -> None:
        reason = self.readiness()
        if (
            self._authorization is None
            or self._now() >= self._authorization_deadline
            or reason is not None
        ):
            self.abort(reason or "control authorization unavailable/expired")
            raise RuntimeError(self.fault)

    def _command(
        self,
        command: int,
        parameters: tuple[float, ...],
        request_id: str,
        *,
        read_only: bool = False,
    ) -> CommandTransaction:
        if not read_only:
            self._control_ready()
        if command in self._used_commands:
            raise RuntimeError(
                "command ID already used this session; no ambiguous retries"
            )
        if self.transaction is not None and self.transaction.result is None:
            raise RuntimeError("another command transaction is pending")
        now = self._now()
        transaction = CommandTransaction(
            request_id, command, now, now + self.limits.command_timeout_s
        )
        self.transaction = transaction
        self._used_commands.add(command)
        self._wire.command(command, parameters)
        self._emit(
            "command_sent",
            {
                "request_id": request_id,
                "command": command,
                "parameters": list(parameters),
            },
        )
        return transaction

    def offboard(self, request_id: str) -> CommandTransaction:
        return self._command(176, (1, 6, 0, 0, 0, 0, 0), request_id)

    def unavailable_mode_probe(self, request_id: str) -> CommandTransaction:
        """Explicit negative test only: unknown PX4 custom mode, never arm."""
        return self._command(176, (1, 255, 0, 0, 0, 0, 0), request_id)

    def arm(self, request_id: str) -> CommandTransaction:
        return self._command(400, (1, 0, 0, 0, 0, 0, 0), request_id)

    def land(self, request_id: str) -> CommandTransaction:
        return self._command(21, (0, 0, 0, 0, 0, 0, 0), request_id)

    def set_target(self, target: LocalPosition, request_id: str | None = None) -> None:
        self._control_ready()
        home = self._home
        if (
            home is None
            or target.origin_id != home.origin_id
            or hypot(target.north_m - home.north_m, target.east_m - home.east_m)
            > self.limits.radius_m
            or not -0.3 <= home.down_m - target.down_m <= self.limits.ceiling_m
        ):
            self.abort("target outside the authorized local volume")
            raise ValueError("target outside the authorized local volume")
        self.target = target
        self._stream_request_id = request_id

    def stream(self) -> None:
        if self.target is None:
            return
        self._control_ready()
        now = self._now()
        if self._last_stream is not None and now - self._last_stream >= 0.25:
            self.abort("setpoint scheduler gap >=0.25 seconds")
            raise RuntimeError(self.fault)
        if (
            self._last_stream is not None
            and now - self._last_stream < self.limits.stream_interval_s
        ):
            return
        state, home = self.snapshot(), self._home
        if state is None or state.position is None or home is None:
            raise RuntimeError("position disappeared before stream")
        position = state.position
        if (
            position.origin_id != home.origin_id
            or hypot(position.north_m - home.north_m, position.east_m - home.east_m)
            > self.limits.radius_m
            or not -0.5 <= home.down_m - position.down_m <= self.limits.ceiling_m
        ):
            self.abort("measured position outside authorized volume/session")
            raise RuntimeError(self.fault)
        boot_ms = int((self._remote_s + now - self._aligned_at) * 1000) % 2**32
        target = self.target
        self._wire.setpoint(boot_ms, target.north_m, target.east_m, target.down_m)
        self._last_stream = now
        self._emit(
            "setpoint_sent",
            {
                "request_id": self._stream_request_id,
                "authority_id": self._authorization,
                "source_id": state.source_id,
                "mode": list(self.heartbeat.mode) if self.heartbeat else None,
                "armed": self.heartbeat.armed if self.heartbeat else None,
                "boot_ms": boot_ms,
                "target_ned_m": [target.north_m, target.east_m, target.down_m],
                "position_ned_m": [position.north_m, position.east_m, position.down_m],
            },
        )

    def stop_stream(self) -> None:
        self.target = None
        self._stream_request_id = None
        self._last_stream = None

    def abort(self, reason: str) -> None:
        self.stop_stream()
        self._authorization = None
        if self.fault is None:
            self.fault = reason
            self._emit(
                "authority_revoked",
                {
                    "reason": reason,
                    "local_setpoints_stopped": True,
                    "physical_stop_confirmed": False,
                },
            )
