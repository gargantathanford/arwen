"""Deterministic contract examples with no transport or hardware access."""

from collections.abc import Callable
from dataclasses import FrozenInstanceError, dataclass, field, replace

import pytest

from arwen.config.models import TimingConfig
from arwen.vehicle.contracts import (
    Capabilities,
    LocalPosition,
    PositionRequest,
    RequestResult,
    RequestStatus,
    VehicleAdapter,
    VehicleState,
)


@dataclass
class FakeAdapter:
    clock: Callable[[], float]
    state: VehicleState | None
    config: TimingConfig
    capabilities: Capabilities = field(default_factory=Capabilities)
    authority: str | None = None
    accepted: list[PositionRequest] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)

    def snapshot(self) -> VehicleState | None:
        return self.state

    def submit(self, position_request: PositionRequest) -> RequestResult:
        now = self.clock()
        status = RequestStatus.ACCEPTED
        if position_request.request_id in self.seen:
            status = RequestStatus.DUPLICATE
        elif not self.capabilities.local_position_requests:
            status = RequestStatus.UNSUPPORTED
        elif now < position_request.issued_at:
            status = RequestStatus.INVALID_TIME
        elif now >= position_request.expires_at:
            status = RequestStatus.EXPIRED
        elif position_request.authority_id != self.authority:
            status = RequestStatus.NO_AUTHORITY
        elif self.state is None or not self.state.position_is_fresh(
            now, self.config.max_state_age_s
        ):
            status = RequestStatus.UNAVAILABLE_STATE
        elif (
            self.state.position is None
            or self.state.position.origin_id != position_request.target.origin_id
        ):
            status = RequestStatus.FRAME_MISMATCH
        self.seen.add(position_request.request_id)
        if status is RequestStatus.ACCEPTED:
            self.accepted.append(position_request)
        return RequestResult(position_request.request_id, status)


@pytest.fixture
def adapter() -> FakeAdapter:
    return FakeAdapter(
        lambda: 10.0,
        VehicleState("fake", 9.0, 9.5, LocalPosition(0, 0, 0, "home")),
        TimingConfig(2, 3),
        Capabilities(True),
        "grant",
    )


@pytest.fixture
def position_request() -> PositionRequest:
    return PositionRequest("req", "grant", LocalPosition(1, 2, -3, "home"), 9, 12)


def test_submission_is_not_movement(
    adapter: FakeAdapter, position_request: PositionRequest
) -> None:
    boundary: VehicleAdapter = adapter
    before = boundary.snapshot()
    result = boundary.submit(position_request)
    assert result == RequestResult("req", RequestStatus.ACCEPTED)
    assert boundary.snapshot() == before
    assert adapter.accepted == [position_request]
    assert boundary.submit(position_request).status is RequestStatus.DUPLICATE


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"expires_at": 10}, RequestStatus.EXPIRED),
        ({"issued_at": 11}, RequestStatus.INVALID_TIME),
        ({"authority_id": "other"}, RequestStatus.NO_AUTHORITY),
        ({"target": LocalPosition(0, 0, 0, "other")}, RequestStatus.FRAME_MISMATCH),
    ],
)
def test_request_rejections(
    adapter: FakeAdapter,
    position_request: PositionRequest,
    change: dict[str, object],
    expected: RequestStatus,
) -> None:
    assert adapter.submit(replace(position_request, **change)).status is expected
    assert adapter.accepted == []


def test_authority_revocation(
    adapter: FakeAdapter, position_request: PositionRequest
) -> None:
    assert adapter.submit(position_request).status is RequestStatus.ACCEPTED
    adapter.authority = None
    assert adapter.submit(replace(position_request, request_id="next")).status is (
        RequestStatus.NO_AUTHORITY
    )
    assert len(adapter.accepted) == 1


def test_unsupported(adapter: FakeAdapter, position_request: PositionRequest) -> None:
    adapter.capabilities = Capabilities()
    assert adapter.submit(position_request).status is RequestStatus.UNSUPPORTED
    assert not adapter.accepted


@pytest.mark.parametrize("position", [None, LocalPosition(0, 0, 0, "home")])
def test_stale_measurement_not_refreshed_by_receipt(
    adapter: FakeAdapter,
    position_request: PositionRequest,
    position: LocalPosition | None,
) -> None:
    adapter.state = VehicleState("fake", 8, 10, position)
    assert adapter.submit(position_request).status is RequestStatus.UNAVAILABLE_STATE
    assert not adapter.accepted


def test_missing_state(adapter: FakeAdapter, position_request: PositionRequest) -> None:
    adapter.state = None
    assert adapter.submit(position_request).status is RequestStatus.UNAVAILABLE_STATE


def test_freshness_boundaries() -> None:
    state = VehicleState("fake", 8, 9, LocalPosition(0, 0, 0, "home"))
    assert state.position_is_fresh(9, 2)
    assert not state.position_is_fresh(10, 2)
    assert not state.position_is_fresh(8, 2)
    assert not replace(state, position=None).position_is_fresh(9, 2)
    with pytest.raises(FrozenInstanceError):
        state.position = None


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_invalid_configuration(value: float) -> None:
    with pytest.raises(ValueError):
        TimingConfig(value, 1)
    with pytest.raises(ValueError):
        TimingConfig(1, value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_invalid_coordinates(value: float) -> None:
    with pytest.raises(ValueError):
        LocalPosition(value, 0, 0, "home")


def test_invalid_identity_and_time(position_request: PositionRequest) -> None:
    with pytest.raises(ValueError):
        LocalPosition(0, 0, 0, " ")
    with pytest.raises(ValueError):
        VehicleState("fake", 10, 9, None)
    for changes in (
        {"request_id": ""},
        {"authority_id": " "},
        {"expires_at": 9},
        {"issued_at": float("nan")},
    ):
        with pytest.raises(ValueError):
            replace(position_request, **changes)
