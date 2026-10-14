"""Synchronous passive PX4 interpretation with conservative clock alignment."""

from collections.abc import Callable
from enum import Enum
from math import isfinite
from uuid import uuid4

from arwen.communication.messages import (
    Heartbeat,
    Packet,
    Position,
    Timesync,
    Transport,
)
from arwen.config.telemetry import TelemetryConfig
from arwen.vehicle.contracts import (
    Capabilities,
    LocalPosition,
    PositionRequest,
    RequestResult,
    RequestStatus,
    VehicleState,
)


class ConnectionState(Enum):
    CLOSED = "closed"
    CONNECTING = "connecting"
    HEALTHY = "healthy"
    LOST = "lost"
    FAILED = "failed"


class TimingQuality(Enum):
    UNCERTAIN = "uncertain"
    BOUNDED = "bounded"


class Px4Adapter:
    """One owner must call open/update/close; no threads or automatic retries."""

    def __init__(
        self, transport: Transport, config: TelemetryConfig, clock: Callable[[], float]
    ) -> None:
        self._transport = transport
        self._config = config
        self._clock = clock
        self._status = ConnectionState.CLOSED
        self.failure: str | None = None
        self._opened = 0.0
        self._heartbeat: float | None = None
        self._peer: tuple[str, int] | None = None
        self._state: VehicleState | None = None
        self._session = ""
        self._offset: tuple[float, float] | None = None
        self._aligned_at = 0.0
        self._remote_s = 0.0
        self._last_boot_s: float | None = None
        self._pending: tuple[int, float] | None = None
        self._last_sync = -float("inf")

    def _now(self) -> float:
        now = self._clock()
        if not isfinite(now) or now < 0:
            raise ValueError("Clock must provide finite nonnegative monotonic seconds")
        return now

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(local_position_requests=False)

    @property
    def connection_state(self) -> ConnectionState:
        self._check_health(self._now())
        return self._status

    @property
    def timing_quality(self) -> TimingQuality:
        now = self._now()
        self._check_health(now)
        if (
            self._offset is not None
            and now - self._aligned_at < self._config.sync_valid_s
        ):
            return TimingQuality.BOUNDED
        return TimingQuality.UNCERTAIN

    def submit(self, request: PositionRequest) -> RequestResult:
        return RequestResult(request.request_id, RequestStatus.UNSUPPORTED)

    def snapshot(self) -> VehicleState | None:
        self._check_health(self._now())
        if self._state is not None and self.timing_quality is TimingQuality.UNCERTAIN:
            state = self._state
            return VehicleState(
                state.source_id, state.measured_at, state.received_at, None
            )
        return self._state

    def _new_session(self) -> None:
        self._session = uuid4().hex
        self._state = None
        self._offset = None
        self._last_boot_s = None
        self._pending = None

    def open(self) -> None:
        if self._status in (ConnectionState.CONNECTING, ConnectionState.HEALTHY):
            raise RuntimeError("Adapter already open; close before reconnecting")
        self._new_session()
        self._heartbeat = None
        self._peer = None
        self.failure = None
        self._opened = self._now()
        self._last_sync = -float("inf")
        try:
            self._transport.open()
        except (OSError, RuntimeError) as exc:
            self._terminate(ConnectionState.FAILED, f"UDP open failed: {exc}")
            raise
        self._status = ConnectionState.CONNECTING

    def close(self) -> None:
        self._terminate(ConnectionState.CLOSED, None)

    def _terminate(self, status: ConnectionState, reason: str | None) -> None:
        self._transport.close()
        self._new_session()
        self._heartbeat = None
        self._peer = None
        self._status = status
        self.failure = reason

    def _check_health(self, now: float) -> None:
        if self._status is ConnectionState.CONNECTING:
            if now - self._opened >= self._config.connection_timeout_s:
                self._terminate(
                    ConnectionState.LOST,
                    "No expected PX4 heartbeat before connection timeout",
                )
        elif self._status is ConnectionState.HEALTHY and self._heartbeat is not None:
            if now - self._heartbeat >= self._config.heartbeat_timeout_s:
                self._terminate(
                    ConnectionState.LOST, "Selected PX4 heartbeat timed out"
                )

    def update(self) -> None:
        """Poll at most once with wait; drain at most 63 further decoded packets."""
        self._check_health(self._now())
        if self._status not in (ConnectionState.CONNECTING, ConnectionState.HEALTHY):
            return
        try:
            for index in range(64):
                packet = self._transport.receive(
                    self._config.poll_s if index == 0 else 0
                )
                now = self._now()
                self._check_health(now)
                if self.connection_state not in (
                    ConnectionState.CONNECTING,
                    ConnectionState.HEALTHY,
                ):
                    return
                if packet is None:
                    break
                self._consume(packet, now)
            now = self._now()
            if self._status is ConnectionState.HEALTHY:
                if now - self._last_sync >= self._config.sync_interval_s:
                    stamp = max(1, int(now * 1_000_000_000))
                    self._pending = (stamp, now)
                    self._last_sync = now
                    self._transport.request_timesync(stamp)
        except (OSError, RuntimeError) as exc:
            self._terminate(ConnectionState.FAILED, f"UDP receive/send failed: {exc}")
            raise

    def _consume(self, packet: Packet, now: float) -> None:
        if (packet.system, packet.component) != (
            self._config.system_id,
            self._config.component_id,
        ) or (self._peer is not None and packet.peer != self._peer):
            return
        payload = packet.payload
        if isinstance(payload, Heartbeat):
            if payload.autopilot != 12 or payload.vehicle_type not in (
                1,
                2,
                3,
                4,
                7,
                10,
                11,
                12,
                13,
                14,
                15,
                16,
                17,
                19,
                20,
                21,
                22,
                23,
                24,
                25,
            ):
                return
            if self._peer is None:
                self._peer = packet.peer
                self._transport.select_peer(packet.peer)
            self._heartbeat = now
            self._status = ConnectionState.HEALTHY
            if self._state is None:
                self._state = VehicleState(self._source_id, now, now, None)
        elif self._status is ConnectionState.HEALTHY:
            if isinstance(payload, Timesync):
                self._synchronize(payload, now)
            elif isinstance(payload, Position):
                self._position(payload, now)

    @property
    def _source_id(self) -> str:
        return f"px4:{self._config.system_id}:{self._config.component_id}"

    def _synchronize(self, message: Timesync, now: float) -> None:
        if self._pending is None or message.echo_ns != self._pending[0]:
            return
        sent = self._pending[1]
        self._pending = None
        rtt = now - sent
        if message.remote_ns <= 0 or not 0 <= rtt <= self._config.sync_max_rtt_s:
            return
        remote = message.remote_ns / 1_000_000_000
        low, high = sent - remote, now - remote
        if self._offset is not None:
            drift = max(0, now - self._aligned_at) * 0.0001 + 0.001
            if (
                remote < self._remote_s
                or low > self._offset[1] + drift
                or high < self._offset[0] - drift
            ):
                self._new_session()
                self._state = VehicleState(self._source_id, now, now, None)
        self._offset = (low, high)
        self._aligned_at = now
        self._remote_s = remote

    def _position(self, message: Position, now: float) -> None:
        if type(message.boot_ms) is not int or not 0 <= message.boot_ms < 2**32:
            return
        if not all(isfinite(v) for v in (message.north, message.east, message.down)):
            return
        if self.timing_quality is TimingQuality.UNCERTAIN or self._offset is None:
            return
        wrap_s = 2**32 / 1000
        raw = message.boot_ms / 1000
        expected = self._remote_s + now - self._aligned_at
        boot = raw + round((expected - raw) / wrap_s) * wrap_s
        if boot < 0 or (self._last_boot_s is not None and boot <= self._last_boot_s):
            return
        drift = (now - self._aligned_at + abs(boot - self._remote_s)) * 0.0001
        measured = boot + self._offset[0] - 0.001 - drift
        latest = boot + self._offset[1] + drift
        if measured < 0 or latest > now + 0.001:
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
