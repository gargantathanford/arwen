"""Wire and deterministic flight safety tests, never simulator acceptance."""

from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

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
from arwen.communication.messages import Packet, Position, Timesync, WirePayload
from arwen.communication.px4_sitl import REQUIRED_PARAMETERS, Px4SitlAdapter
from arwen.config.sitl import SitlConfig
from arwen.config.telemetry import TelemetryConfig
from arwen.control.arrival import ArrivalTracker
from arwen.logging.events import JsonEvents
from arwen.vehicle.contracts import (
    LocalPosition,
    PositionRequest,
    RequestStatus,
    VehicleState,
)


@dataclass
class Clock:
    now: float = 100

    def __call__(self) -> float:
        return self.now


class FakeWire(FlightUdpTransport):
    def __init__(self) -> None:
        super().__init__(TelemetryConfig())
        self.packets: deque[Packet] = deque()
        self.outputs: list[tuple[str, object]] = []
        self.opened = False

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.opened = False
        self.packets.clear()

    def receive(self, timeout_s: float) -> Packet | None:
        return self.packets.popleft() if self.packets else None

    def request_timesync(self, stamp_ns: int) -> None:
        self.outputs.append(("timesync", stamp_ns))

    def command(self, command: int, parameters: tuple[float, ...]) -> None:
        self.outputs.append(("command", (command, parameters)))

    def parameter_read(self, name: str) -> None:
        self.outputs.append(("parameter_read", name))

    def setpoint(self, boot_ms: int, north: float, east: float, down: float) -> None:
        self.outputs.append(("setpoint", (boot_ms, north, east, down)))


@dataclass
class Rig:
    adapter: Px4SitlAdapter
    wire: FakeWire
    clock: Clock
    events: list[tuple[str, dict[str, object]]]

    def feed(self, payload: WirePayload, *, system: int = 1) -> None:
        self.wire.packets.append(Packet(system, 1, ("127.0.0.1", 14580), payload))


@pytest.fixture
def rig() -> Rig:
    clock, wire = Clock(), FakeWire()
    events: list[tuple[str, dict[str, object]]] = []

    def emit(event: str, fields: dict[str, object]) -> None:
        events.append((event, fields))

    adapter = Px4SitlAdapter(
        wire, TelemetryConfig(heartbeat_timeout_s=10), SitlConfig(), clock, emit
    )
    adapter.open()
    rig = Rig(adapter, wire, clock, events)
    rig.feed(FlightHeartbeat(12, 2, 1, 3 << 16, 3))
    adapter.poll()
    stamp = next(value for kind, value in wire.outputs if kind == "timesync")
    assert isinstance(stamp, int)
    clock.now += 0.02
    for message in (
        Timesync(10_000_000_000, stamp),
        Position(10000, 0, 0, 0),
        LandedState(1),
        Version(0x011000FF, 128),
        Odometry(10_000_000, 1, 0),
        Estimator(10_000_000, 41),
        CommandAck(512, 0, 255, 191),
    ):
        rig.feed(message)
    for name, value in REQUIRED_PARAMETERS.items():
        rig.feed(Parameter(name, value))
    adapter.poll()
    assert adapter.readiness() is None
    return rig


def authorize(rig: Rig) -> None:
    rig.adapter.authorize("explicit-operator-grant", 200, operator_confirmed=True)


def test_no_operator_no_motion(rig: Rig) -> None:
    with pytest.raises(RuntimeError):
        rig.adapter.authorize("grant", 200, operator_confirmed=False)
    with pytest.raises(RuntimeError):
        rig.adapter.arm("arm")
    assert not any(kind == "setpoint" for kind, _ in rig.wire.outputs)
    assert [value[0] for kind, value in rig.wire.outputs if kind == "command"] == [512]  # type: ignore[index]


def test_stream_has_no_manufactured_ack(rig: Rig) -> None:
    authorize(rig)
    state = rig.adapter.snapshot()
    assert state is not None and state.position is not None
    rig.adapter.set_target(state.position)
    original = rig.adapter.transaction
    rig.adapter.stream()
    rig.adapter.stream()
    assert len([1 for kind, _ in rig.wire.outputs if kind == "setpoint"]) == 1
    assert rig.adapter.transaction is original
    assert not rig.adapter.capabilities.local_position_requests
    req = PositionRequest("r", "grant", state.position, 100, 101)
    assert rig.adapter.submit(req).status is RequestStatus.UNSUPPORTED
    rig.adapter.abort("takeover")
    before = list(rig.wire.outputs)
    rig.adapter.stream()
    assert rig.wire.outputs == before
    with pytest.raises(RuntimeError):
        rig.adapter.arm("must-not-rearm")


def test_command_correlation_progress_and_no_retry(rig: Rig) -> None:
    authorize(rig)
    transaction = rig.adapter.offboard("mode")
    rig.feed(CommandAck(400, 0, 255, 191))
    rig.feed(CommandAck(176, 0, 254, 191))
    rig.feed(CommandAck(176, 5, 255, 191))
    rig.adapter.poll()
    assert transaction.result is None
    rig.feed(CommandAck(176, 0, 255, 191))
    rig.adapter.poll()
    assert transaction.result == 0
    with pytest.raises(RuntimeError):
        rig.adapter.offboard("retry")


@pytest.mark.parametrize(
    "case", ["timeout", "rejection", "reset", "stale", "gap", "parameters"]
)
def test_flight_failures_stop_output(rig: Rig, case: str) -> None:
    authorize(rig)
    state = rig.adapter.snapshot()
    assert state is not None and state.position is not None
    rig.adapter.set_target(state.position)
    rig.adapter.stream()
    if case in ("timeout", "rejection"):
        transaction = rig.adapter.offboard("mode")
        if case == "timeout":
            rig.clock.now = transaction.deadline
        else:
            rig.feed(CommandAck(176, 2, 255, 191))
        with pytest.raises(RuntimeError):
            rig.adapter.poll()
    elif case == "reset":
        rig.feed(Odometry(10_100_000, 1, 1))
        rig.adapter.poll()
    elif case == "stale":
        rig.clock.now += 0.6
        with pytest.raises(RuntimeError):
            rig.adapter.stream()
    elif case == "gap":
        rig.clock.now += 0.25
        with pytest.raises(RuntimeError):
            rig.adapter.stream()
    else:
        rig.feed(Parameter("COM_OBL_RC_ACT", 0))
        rig.adapter.poll()
        with pytest.raises(RuntimeError):
            rig.adapter.stream()
    before = len([kind for kind, _ in rig.wire.outputs if kind == "setpoint"])
    rig.adapter.stream()
    assert len([kind for kind, _ in rig.wire.outputs if kind == "setpoint"]) == before
    assert rig.adapter.fault is not None


def test_bounds_source_and_reconnection(rig: Rig) -> None:
    authorize(rig)
    rig.feed(FlightHeartbeat(12, 2, 129, 6 << 16, 4), system=2)
    rig.adapter.poll()
    assert rig.adapter.heartbeat is not None and not rig.adapter.heartbeat.armed
    state = rig.adapter.snapshot()
    assert state is not None and state.position is not None
    with pytest.raises(ValueError):
        rig.adapter.set_target(replace(state.position, north_m=6))
    rig.adapter.close()
    rig.adapter.open()
    with pytest.raises(RuntimeError):
        rig.adapter.arm("no-auto-rearm")


def test_arrival_needs_distinct_fresh_dwell() -> None:
    limits = SitlConfig(dwell_s=0.3)
    tracker = ArrivalTracker(limits)
    target = LocalPosition(1, 2, -2, "session")

    def sample(stamp: float, position: LocalPosition = target) -> VehicleState:
        return VehicleState("vehicle", stamp, stamp, position)

    assert not tracker.update(sample(1), target, 1)
    assert not tracker.update(sample(1), target, 1.4)
    assert not tracker.update(sample(1.1), target, 1.1)
    assert tracker.update(sample(1.4), target, 1.4)
    assert not tracker.update(sample(1.5, replace(target, north_m=2)), target, 1.5)
    assert not tracker.update(sample(1.6), target, 1.6)
    assert not tracker.update(sample(2.3), target, 2.3)  # evidence gap resets dwell
    assert not tracker.update(sample(2.4), replace(target, origin_id="other"), 2.4)


@pytest.mark.parametrize(
    "changes",
    [
        {"height_m": 20},
        {"north_m": 6},
        {"dwell_s": 0},
        {"stream_interval_s": 0.5},
        {"radius_m": float("nan")},
    ],
)
def test_config_rejects_unbounded_motion(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        replace(SitlConfig(), **changes)


def test_structured_events_are_explicit_and_exclusive(tmp_path: Path) -> None:
    import json

    path = tmp_path / "events.jsonl"
    events = JsonEvents(path, Clock())
    events.write(
        "transition",
        {
            "mode": [6, 0],
            "armed": True,
            "request_id": "r",
            "position_ned_m": [0, 0, -2],
        },
    )
    events.close()
    record = json.loads(path.read_text())
    assert record["monotonic_s"] == 100 and record["mode"] == [6, 0]
    with pytest.raises(FileExistsError):
        JsonEvents(path, Clock())


def test_real_flight_wire_semantics() -> None:
    import struct

    mav = pytest.importorskip("pymavlink.dialects.v20.common")
    from arwen.communication.mavlink import FlightCodec

    codec = FlightCodec()
    parser = mav.MAVLink(None)
    frame = parser.parse_buffer(codec.setpoint(1, 1, 10000, 1, 2, -2))[0]
    assert frame.get_type() == "SET_POSITION_TARGET_LOCAL_NED"
    assert (frame.coordinate_frame, frame.type_mask, frame.time_boot_ms) == (
        1,
        3576,
        10000,
    )
    assert (frame.x, frame.y, frame.z) == (1, 2, -2)
    for command, params in (
        (400, (1, 21196, 0, 0, 0, 0, 0)),
        (176, (1, 3, 0, 0, 0, 0, 0)),
        (22, (0, 0, 0, 0, 0, 0, 0)),
    ):
        with pytest.raises(ValueError):
            codec.command(1, 1, command, params)
    message = mav.MAVLink_param_value_message(
        b"SYS_AUTOSTART", struct.unpack("<f", struct.pack("<i", 10040))[0], 6, 1, 0
    )
    decoded = codec.decode(
        message.pack(mav.MAVLink(None, srcSystem=1, srcComponent=1)),
        ("127.0.0.1", 14580),
    )
    assert decoded[0].payload == Parameter("SYS_AUTOSTART", 10040)


def test_nonaccelerated_sih_causal_alignment(rig: Rig) -> None:
    original = rig.adapter.snapshot()
    assert original is not None and original.position is not None
    authorize(rig)
    rig.clock.now = 101.02
    rig.adapter._pending = (123, 101.0)
    rig.adapter._synchronize(Timesync(10_980_000_000, 123), 101.02)
    # Offset grows as SIH slows; this is not a controller restart. A delayed
    # measurement preceding the new anchor must still use the old lower bound.
    assert rig.adapter._measurement_time(10.5) == pytest.approx(100.495)
    rig.feed(Position(11000, 0, 0, 0))
    rig.adapter.poll()
    updated = rig.adapter.snapshot()
    assert updated is not None and updated.position is not None
    assert original.position.origin_id == updated.position.origin_id
    assert rig.adapter._measurement_time(9.9) is None
    rig.adapter._pending = (124, 101.1)
    rig.adapter._synchronize(Timesync(500_000_000, 124), 101.12)
    assert rig.adapter.fault is not None
    assert rig.adapter.target is None


def test_estimator_fault_flags_deny_control(rig: Rig) -> None:
    rig.feed(Estimator(10_100_000, 41 | 1024))
    rig.adapter.poll()
    assert rig.adapter.readiness() is not None
    with pytest.raises(RuntimeError):
        authorize(rig)


def test_preflight_reset_replaces_frame_without_grant(rig: Rig) -> None:
    old = rig.adapter.snapshot()
    rig.feed(Odometry(10_100_000, 1, 1))
    rig.adapter.poll()
    assert rig.adapter.fault is None
    assert rig.adapter.snapshot() is None
    rig.clock.now += 0.1
    rig.feed(Position(10100, 0, 0, 0))
    rig.adapter.poll()
    new = rig.adapter.snapshot()
    assert old is not None and new is not None
    assert old.position is not None and new.position is not None
    assert old.position.origin_id != new.position.origin_id


@pytest.mark.parametrize(
    ("armed", "landed", "phase", "permitted"),
    [
        (False, 1, "WAIT_LANDED_DISARMED", True),
        (True, 1, "WAIT_LANDED_DISARMED", False),
        (False, 2, "WAIT_LANDED_DISARMED", False),
        (False, 1, "MOVING", False),
    ],
)
def test_ground_mode_cleanup_cannot_mask_airborne_takeover(
    rig: Rig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    armed: bool,
    landed: int,
    phase: str,
    permitted: bool,
) -> None:
    from arwen.applications import px4_sitl
    from arwen.vehicle.authority import AuthorityEvidence, AuthorityMode

    monkeypatch.setattr(px4_sitl, "monotonic", rig.clock)
    monkeypatch.setattr(px4_sitl, "sleep", lambda _: None)
    log = JsonEvents(tmp_path / "flight.jsonl", rig.clock)
    scenario = px4_sitl.SitlScenario(rig.adapter, SitlConfig(), log)
    state = rig.adapter.snapshot()
    assert state is not None
    scenario.authority.update(
        state,
        AuthorityEvidence(
            state.source_id,
            rig.clock.now,
            True,
            AuthorityMode.AUTONOMY_PERMITTED,
        ),
    )
    scenario.grant = scenario.authority.grant("operator", 100)
    authorize(rig)
    scenario.phase = phase
    scenario._allowed_modes = {(4, 6)}
    rig.feed(FlightHeartbeat(12, 2, 129 if armed else 1, 6 << 16, 3))
    rig.feed(LandedState(landed))
    try:
        if permitted:
            scenario.pump()
        else:
            with pytest.raises(RuntimeError, match="takeover"):
                scenario.pump()
    finally:
        log.close()
