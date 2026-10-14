"""Passive behavior with a deterministic clock and transport."""

from collections import deque
from dataclasses import dataclass, field, replace

import pytest

from arwen.communication.messages import Heartbeat, Packet, Position, Timesync
from arwen.communication.px4 import ConnectionState, Px4Adapter, TimingQuality
from arwen.config.telemetry import TelemetryConfig
from arwen.vehicle.contracts import (
    LocalPosition,
    PositionRequest,
    RequestStatus,
    VehicleAdapter,
)

PEER = ("127.0.0.1", 18570)


@dataclass
class Clock:
    now: float = 100.0

    def __call__(self) -> float:
        return self.now


@dataclass
class FakeTransport:
    packets: deque[Packet] = field(default_factory=deque)
    sent: list[int] = field(default_factory=list)
    opened: bool = False
    closes: int = 0
    fail: bool = False
    peer: tuple[str, int] | None = None
    polls: list[float] = field(default_factory=list)

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.opened = False
        self.closes += 1
        self.packets.clear()

    def receive(self, timeout_s: float) -> Packet | None:
        self.polls.append(timeout_s)
        if self.fail:
            raise OSError("test receive failure")
        return self.packets.popleft() if self.packets else None

    def select_peer(self, peer: tuple[str, int]) -> None:
        self.peer = peer

    def request_timesync(self, stamp_ns: int) -> None:
        self.sent.append(stamp_ns)


@dataclass
class Rig:
    clock: Clock
    transport: FakeTransport
    adapter: Px4Adapter

    def feed(self, payload: Heartbeat | Position | Timesync) -> None:
        self.transport.packets.append(Packet(1, 1, PEER, payload))
        self.adapter.update()

    def align(self, remote: float = 10.0) -> None:
        stamp = self.transport.sent[-1]
        self.clock.now += 0.02
        self.feed(Timesync(int(remote * 1e9), stamp))


@pytest.fixture
def rig() -> Rig:
    clock = Clock()
    transport = FakeTransport()
    adapter = Px4Adapter(transport, TelemetryConfig(), clock)
    adapter.open()
    return Rig(clock, transport, adapter)


def ready(rig: Rig) -> None:
    rig.feed(Heartbeat(12, 2))
    rig.align()


def test_identity_and_zero(rig: Rig) -> None:
    assert rig.adapter.connection_state is ConnectionState.CONNECTING
    assert rig.adapter.snapshot() is None
    ready(rig)
    rig.feed(Position(10000, 0, 0, 0))
    boundary: VehicleAdapter = rig.adapter
    state = boundary.snapshot()
    assert state is not None and state.position is not None
    assert (state.position.north_m, state.position.east_m, state.position.down_m) == (
        0,
        0,
        0,
    )
    assert state.source_id == "px4:1:1"
    assert state.position.origin_id.startswith("px4:1:1:estimator:")
    assert state.measured_at < state.received_at
    assert state.position_is_fresh(state.measured_at + 1 - 1e-8, 1)
    assert not state.position_is_fresh(state.measured_at + 1, 1)
    rig.feed(Heartbeat(12, 2))
    assert boundary.snapshot() == state


@pytest.mark.parametrize(
    "system,component,autopilot,kind",
    [
        (2, 1, 12, 2),
        (1, 2, 12, 2),
        (1, 1, 3, 2),
        (1, 1, 12, 6),
        (1, 1, 12, 26),
        (1, 1, 12, 18),
    ],
)
def test_reject_identity(
    rig: Rig, system: int, component: int, autopilot: int, kind: int
) -> None:
    rig.transport.packets.append(
        Packet(system, component, PEER, Heartbeat(autopilot, kind))
    )
    rig.adapter.update()
    assert rig.adapter.connection_state is ConnectionState.CONNECTING
    assert rig.adapter.snapshot() is None
    assert rig.transport.sent == []


def test_unaligned_missing_and_wrong_echo(rig: Rig) -> None:
    rig.feed(Position(10000, 1, 2, 3))
    assert rig.adapter.snapshot() is None
    rig.feed(Heartbeat(12, 2))
    rig.feed(Position(10000, 1, 2, 3))
    rig.feed(Timesync(10_000_000_000, 123))
    assert rig.adapter.timing_quality is TimingQuality.UNCERTAIN
    state = rig.adapter.snapshot()
    assert state is not None and state.position is None


@pytest.mark.parametrize(
    "boot,x", [(-1, 1), (2**32, 1), (10000, float("nan")), (10000, float("inf"))]
)
def test_invalid_position(rig: Rig, boot: int, x: float) -> None:
    ready(rig)
    rig.feed(Position(boot, x, 0, 0))
    state = rig.adapter.snapshot()
    assert state is not None and state.position is None


def test_delay_reorder_duplicate(rig: Rig) -> None:
    ready(rig)
    rig.feed(Position(8000, 1, 2, -3))
    old = rig.adapter.snapshot()
    assert old is not None and not old.position_is_fresh(rig.clock.now, 1)
    assert old.position is not None and old.position.down_m == -3
    rig.feed(Position(8000, 9, 9, 9))
    rig.feed(Position(7999, 9, 9, 9))
    assert rig.adapter.snapshot() == old
    rig.feed(Position(10000, 4, 5, -6))
    assert rig.adapter.snapshot() != old


def test_timing_expiry_high_rtt_and_future(rig: Rig) -> None:
    rig.feed(Heartbeat(12, 2))
    rig.clock.now += 0.2
    rig.feed(Timesync(10_000_000_000, rig.transport.sent[-1]))
    assert rig.adapter.timing_quality is TimingQuality.UNCERTAIN
    rig.clock.now += 1
    rig.feed(Heartbeat(12, 2))
    rig.align()
    rig.feed(Position(20000, 1, 2, 3))
    assert rig.adapter.snapshot().position is None  # type: ignore[union-attr]
    rig.clock.now += 5
    rig.feed(Heartbeat(12, 2))
    assert rig.adapter.timing_quality is TimingQuality.UNCERTAIN


def test_loss_unrelated_traffic_and_reconnect(rig: Rig) -> None:
    ready(rig)
    rig.feed(Position(10000, 0, 0, 0))
    first = rig.adapter.snapshot()
    rig.clock.now = 102
    rig.transport.packets.append(Packet(2, 1, PEER, Heartbeat(12, 2)))
    rig.transport.packets.append(Packet(1, 1, ("127.0.0.2", 18570), Heartbeat(12, 2)))
    rig.adapter.update()
    rig.clock.now = 103
    assert rig.adapter.connection_state is ConnectionState.LOST
    assert rig.adapter.snapshot() is None
    assert not rig.transport.opened
    rig.adapter.open()
    ready(rig)
    rig.feed(Position(10000, 0, 0, 0))
    second = rig.adapter.snapshot()
    assert first is not None and second is not None
    assert first.position is not None and second.position is not None
    assert first.position.origin_id != second.position.origin_id
    rig.adapter.close()
    rig.adapter.close()
    assert rig.adapter.connection_state is ConnectionState.CLOSED


def test_restart_and_wrap(rig: Rig) -> None:
    rig.feed(Heartbeat(12, 2))
    rig.align((2**32 - 10) / 1000)
    rig.feed(Position(2**32 - 10, 0, 0, 0))
    first = rig.adapter.snapshot()
    rig.clock.now += 0.04
    rig.feed(Position(10, 1, 0, 0))
    wrapped = rig.adapter.snapshot()
    assert first is not None and wrapped is not None
    assert wrapped.measured_at > first.measured_at
    assert wrapped.position is not None and first.position is not None
    assert wrapped.position.origin_id == first.position.origin_id
    rig.clock.now += 1
    rig.feed(Heartbeat(12, 2))
    rig.align(0.5)
    assert rig.adapter.snapshot().position is None  # type: ignore[union-attr]
    rig.feed(Position(500, 0, 0, 0))
    restarted = rig.adapter.snapshot()
    assert restarted is not None and restarted.position is not None
    assert restarted.position.origin_id != first.position.origin_id


def test_read_only_no_output(rig: Rig) -> None:
    request = PositionRequest("req", "authority", LocalPosition(0, 0, 0, "home"), 1, 2)
    assert not rig.adapter.capabilities.local_position_requests
    assert rig.adapter.submit(request).status is RequestStatus.UNSUPPORTED
    ready(rig)
    before = list(rig.transport.sent)
    assert rig.adapter.submit(request).request_id == "req"
    assert rig.transport.sent == before


def test_timeout_failure_budget(rig: Rig) -> None:
    rig.clock.now += 10
    assert rig.adapter.connection_state is ConnectionState.LOST
    assert "heartbeat" in (rig.adapter.failure or "")
    rig.adapter.open()
    rig.transport.fail = True
    with pytest.raises(OSError):
        rig.adapter.update()
    assert rig.adapter.connection_state is ConnectionState.FAILED
    assert not rig.transport.opened
    rig.transport.fail = False
    rig.adapter.open()
    rig.transport.packets.extend(
        Packet(2, 1, PEER, Heartbeat(12, 2)) for _ in range(100)
    )
    rig.adapter.update()
    assert len(rig.transport.packets) == 36
    assert rig.transport.polls[-64:] == [0.1] + [0] * 63
    with pytest.raises(RuntimeError):
        rig.adapter.open()


@pytest.mark.parametrize(
    "changes",
    [
        {"bind_host": "example.com"},
        {"bind_port": 0},
        {"system_id": 256},
        {"component_id": 0},
        {"poll_s": 2},
        {"poll_s": float("nan")},
        {"heartbeat_timeout_s": 0},
        {"connection_timeout_s": -1},
        {"display_interval_s": float("inf")},
        {"sync_interval_s": 0.01},
    ],
)
def test_config_validation(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        replace(TelemetryConfig(), **changes)


def test_alignment_expires_without_heartbeat_loss(rig: Rig) -> None:
    ready(rig)
    rig.feed(Position(10000, 1, 2, 3))
    for _ in range(5):
        rig.clock.now += 1
        rig.feed(Heartbeat(12, 2))
    assert rig.adapter.connection_state is ConnectionState.HEALTHY
    assert rig.adapter.timing_quality is TimingQuality.UNCERTAIN
    state = rig.adapter.snapshot()
    assert state is not None and state.position is None


def test_foreign_timesync_cannot_align(rig: Rig) -> None:
    rig.feed(Heartbeat(12, 2))
    rig.transport.packets.append(
        Packet(2, 1, PEER, Timesync(10_000_000_000, rig.transport.sent[-1]))
    )
    rig.adapter.update()
    assert rig.adapter.timing_quality is TimingQuality.UNCERTAIN


def test_forward_clock_discontinuity_invalidates_position(rig: Rig) -> None:
    ready(rig)
    rig.feed(Position(10000, 1, 2, 3))
    before = rig.adapter.snapshot()
    rig.clock.now += 1
    rig.feed(Heartbeat(12, 2))
    rig.align(50)
    state = rig.adapter.snapshot()
    assert state is not None and state.position is None
    rig.feed(Position(50000, 0, 0, 0))
    after = rig.adapter.snapshot()
    assert before is not None and before.position is not None
    assert after is not None and after.position is not None
    assert before.position.origin_id != after.position.origin_id
