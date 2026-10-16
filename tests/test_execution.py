"""Execution lifecycle using explicit synthetic acknowledgements and telemetry."""

from dataclasses import dataclass, replace

import pytest

from arwen.applications.authority_demo import ScenarioClock
from arwen.applications.execution_demo import ScriptedVehicle
from arwen.config.execution import ExecutionConfig
from arwen.config.models import TimingConfig
from arwen.config.safety import SafetyConfig
from arwen.control.authority import AuthorityController, ControlReason
from arwen.control.execution import ExecutionController, ExecutionPhase
from arwen.vehicle.authority import AuthorityEvidence, AuthorityMode
from arwen.vehicle.contracts import (
    Capabilities,
    LocalPosition,
    PositionRequest,
    RequestResult,
    RequestStatus,
    VehicleState,
)
from arwen.vehicle.execution import AckStatus, CommandAcknowledgement, StopStatus


@dataclass
class Rig:
    clock: ScenarioClock
    vehicle: ScriptedVehicle
    authority: AuthorityController
    execution: ExecutionController
    evidence: AuthorityEvidence
    request: PositionRequest

    def ack(self, **changes: object) -> None:
        ack = CommandAcknowledgement(
            "request", "vehicle", "origin", self.clock.now, AckStatus.ACCEPTED
        )
        self.vehicle.feedback.append(replace(ack, **changes))

    def advance(self, delta: float, target: bool = False) -> None:
        self.clock.now += delta
        self.evidence = replace(self.evidence, observed_at=self.clock.now)
        position = self.request.target if target else LocalPosition(0, 0, 0, "origin")
        self.vehicle.state = VehicleState(
            "vehicle", self.clock.now, self.clock.now, position
        )


@pytest.fixture
def rig() -> Rig:
    clock = ScenarioClock()
    vehicle = ScriptedVehicle(
        VehicleState("vehicle", 100, 100, LocalPosition(0, 0, 0, "origin"))
    )
    authority = AuthorityController(SafetyConfig(TimingConfig(1, 3), 1, 5), clock)
    evidence = AuthorityEvidence("vehicle", 100, True, AuthorityMode.AUTONOMY_PERMITTED)
    authority.update(vehicle.state, evidence)
    grant = authority.grant("test", 3)
    request = PositionRequest(
        "request", grant.authority_id, LocalPosition(1, 2, -3, "origin"), 100, 103
    )
    execution = ExecutionController(
        authority, vehicle, ExecutionConfig(0.5, 2, 0.1), clock
    )
    return Rig(clock, vehicle, authority, execution, evidence, request)


def test_submission_ack_and_completion_are_separate(rig: Rig) -> None:
    assert (
        rig.execution.start(rig.request, rig.evidence).phase is ExecutionPhase.SUBMITTED
    )
    assert rig.vehicle.submissions == [rig.request]
    rig.advance(0.1, target=True)
    assert rig.execution.update(rig.evidence).phase is ExecutionPhase.SUBMITTED  # type: ignore[union-attr]
    rig.ack()
    result = rig.execution.update(rig.evidence)
    assert result is not None and result.phase is ExecutionPhase.COMPLETED
    assert rig.vehicle.stops == ["request"]
    assert rig.execution.update(rig.evidence) is None
    assert (
        rig.execution.start(rig.request, rig.evidence).phase is ExecutionPhase.REJECTED
    )
    assert len(rig.vehicle.submissions) == 1


def test_ack_does_not_manufacture_arrival(rig: Rig) -> None:
    rig.execution.start(rig.request, rig.evidence)
    rig.ack()
    outcome = rig.execution.update(rig.evidence)
    assert outcome is not None and outcome.phase is ExecutionPhase.ACKNOWLEDGED
    assert (
        rig.vehicle.state is not None
        and rig.vehicle.state.position != rig.request.target
    )


def test_cached_target_does_not_complete(rig: Rig) -> None:
    rig.vehicle.state = VehicleState("vehicle", 100, 100, rig.request.target)
    rig.execution.start(rig.request, rig.evidence)
    rig.ack()
    assert rig.execution.update(rig.evidence).phase is ExecutionPhase.ACKNOWLEDGED  # type: ignore[union-attr]
    rig.advance(0.1, target=True)
    assert rig.execution.update(rig.evidence).phase is ExecutionPhase.COMPLETED  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "changes",
    [
        {"request_id": "other"},
        {"source_id": "other"},
        {"origin_id": "other"},
        {"observed_at": 99},
        {"observed_at": 101},
    ],
)
def test_unrelated_delayed_or_future_feedback(
    rig: Rig, changes: dict[str, object]
) -> None:
    rig.execution.start(rig.request, rig.evidence)
    rig.ack(**changes)
    rig.advance(0.1, target=True)
    result = rig.execution.update(rig.evidence)
    assert result is not None and result.phase is ExecutionPhase.SUBMITTED


def test_feedback_rejection(rig: Rig) -> None:
    rig.execution.start(rig.request, rig.evidence)
    rig.ack(status=AckStatus.REJECTED)
    outcome = rig.execution.update(rig.evidence)
    assert outcome is not None and outcome.phase is ExecutionPhase.REJECTED
    assert rig.vehicle.stops == ["request"]


def test_ack_exact_timeout(rig: Rig) -> None:
    rig.execution.start(rig.request, rig.evidence)
    rig.advance(0.5)
    rig.ack()
    result = rig.execution.update(rig.evidence)
    assert result is not None and result.phase is ExecutionPhase.ACK_TIMEOUT
    assert rig.vehicle.stops == ["request"]


def test_completion_timeout(rig: Rig) -> None:
    rig.execution.start(rig.request, rig.evidence)
    rig.ack()
    rig.execution.update(rig.evidence)
    for _ in range(4):
        rig.advance(0.5)
        result = rig.execution.update(rig.evidence)
    assert result is not None and result.phase is ExecutionPhase.COMPLETION_TIMEOUT
    assert rig.vehicle.stops == ["request"]


@pytest.mark.parametrize(
    "mode,healthy",
    [
        (AuthorityMode.OPERATOR, True),
        (AuthorityMode.FAILSAFE, True),
        (AuthorityMode.UNKNOWN, True),
        (AuthorityMode.AUTONOMY_PERMITTED, False),
    ],
)
def test_takeover_loss_stops_tracking(
    rig: Rig, mode: AuthorityMode, healthy: bool
) -> None:
    rig.execution.start(rig.request, rig.evidence)
    rig.advance(0.1, target=True)
    rig.ack()
    evidence = replace(rig.evidence, mode=mode, link_healthy=healthy)
    outcome = rig.execution.update(evidence)
    assert outcome is not None and outcome.phase is ExecutionPhase.INTERRUPTED
    assert rig.vehicle.stops == ["request"]
    rig.advance(0.1, target=True)
    assert rig.execution.update(rig.evidence) is None
    assert len(rig.vehicle.submissions) == 1


@pytest.mark.parametrize("case", ["stale", "session", "source", "unknown"])
def test_bad_state_interrupts(rig: Rig, case: str) -> None:
    rig.execution.start(rig.request, rig.evidence)
    if case == "stale":
        rig.clock.now += 1
    elif case == "session":
        rig.vehicle.state = VehicleState(
            "vehicle", 100, 100, LocalPosition(0, 0, 0, "new")
        )
    elif case == "source":
        rig.vehicle.state = VehicleState("other", 100, 100, rig.request.target)
    else:
        rig.vehicle.state = None
    result = rig.execution.update(rig.evidence)
    assert result is not None and result.phase is ExecutionPhase.INTERRUPTED
    assert rig.vehicle.stops == ["request"]


def test_external_revocation_after_dispatch(rig: Rig) -> None:
    rig.execution.start(rig.request, rig.evidence)
    rig.authority.revoke()
    result = rig.execution.update(rig.evidence)
    assert result is not None and result.phase is ExecutionPhase.INTERRUPTED


def test_expired_request_interrupts(rig: Rig) -> None:
    req = replace(rig.request, expires_at=100.2)
    rig.execution.start(req, rig.evidence)
    rig.advance(0.2)
    result = rig.execution.update(rig.evidence)
    assert result is not None and result.phase is ExecutionPhase.INTERRUPTED


def test_cancel_close_and_busy(rig: Rig) -> None:
    rig.execution.start(rig.request, rig.evidence)
    assert (
        rig.execution.start(
            replace(rig.request, request_id="second"), rig.evidence
        ).phase
        is ExecutionPhase.REJECTED
    )
    assert rig.execution.cancel().phase is ExecutionPhase.CANCELLED  # type: ignore[union-attr]
    assert rig.execution.cancel() is None
    assert rig.execution.close() is None
    assert rig.execution.close() is None
    assert (
        rig.execution.start(
            replace(rig.request, request_id="third"), rig.evidence
        ).phase
        is ExecutionPhase.REJECTED
    )
    assert rig.vehicle.stops == ["request"]
    assert len(rig.vehicle.submissions) == 1


def test_close_active_stops(rig: Rig) -> None:
    rig.execution.start(rig.request, rig.evidence)
    result = rig.execution.close()
    assert result is not None and result.phase is ExecutionPhase.CANCELLED
    assert rig.vehicle.stops == ["request"]
    assert rig.authority.revalidate(rig.request).reason is ControlReason.CLOSED


def test_capability_and_authority_denials_no_submit(rig: Rig) -> None:
    rig.vehicle.capabilities = Capabilities(False)
    assert (
        rig.execution.start(rig.request, rig.evidence).phase is ExecutionPhase.REJECTED
    )
    assert not rig.vehicle.submissions
    assert not rig.vehicle.stops
    rig.vehicle.capabilities = Capabilities(True)
    req = replace(rig.request, request_id="second", authority_id="forged")
    assert rig.execution.start(req, rig.evidence).phase is ExecutionPhase.REJECTED
    assert not rig.vehicle.submissions


def test_released_request_cannot_be_changed(rig: Rig) -> None:
    rig.authority.queue(rig.request)
    rig.authority.release(rig.request.request_id)
    changed = replace(rig.request, target=LocalPosition(99, 99, 99, "origin"))
    assert rig.authority.revalidate(changed).reason is ControlReason.NO_GRANT
    assert rig.authority.revalidate(rig.request).reason is ControlReason.AUTHORIZED
    rig.authority.revoke()
    assert rig.authority.revalidate(rig.request).reason is ControlReason.NO_GRANT


def test_clock_error_stops_and_latches(rig: Rig) -> None:
    rig.execution.start(rig.request, rig.evidence)
    rig.clock.now = float("nan")
    result = rig.execution.update(rig.evidence)
    assert result is not None and result.phase is ExecutionPhase.FAILED
    assert rig.execution.fault is not None
    assert rig.vehicle.stops == ["request"]
    rig.execution.close()


class UnconfirmedStop(ScriptedVehicle):
    def stop(self, request_id: str) -> StopStatus:
        self.stops.append(request_id)
        return StopStatus.UNCONFIRMED


def test_unconfirmed_stop_blocks_more_dispatch(rig: Rig) -> None:
    vehicle = UnconfirmedStop(rig.vehicle.state)
    execution = ExecutionController(
        rig.authority, vehicle, ExecutionConfig(0.5, 2, 0.1), rig.clock
    )
    execution.start(rig.request, rig.evidence)
    assert execution.cancel().phase is ExecutionPhase.FAILED  # type: ignore[union-attr]
    assert execution.fault is not None
    assert (
        execution.start(replace(rig.request, request_id="second"), rig.evidence).phase
        is ExecutionPhase.REJECTED
    )
    assert len(vehicle.submissions) == 1


class BrokenSubmission(ScriptedVehicle):
    def submit(self, request: PositionRequest) -> RequestResult:
        super().submit(request)
        raise OSError("delivery uncertain")


class WrongCorrelation(ScriptedVehicle):
    def submit(self, request: PositionRequest) -> RequestResult:
        super().submit(request)
        return RequestResult("other", RequestStatus.ACCEPTED)


class RejectedSubmission(ScriptedVehicle):
    def submit(self, request: PositionRequest) -> RequestResult:
        super().submit(request)
        return RequestResult(request.request_id, RequestStatus.UNSUPPORTED)


@pytest.mark.parametrize(
    "backend", [BrokenSubmission, WrongCorrelation, RejectedSubmission]
)
def test_submission_failure(rig: Rig, backend: type[ScriptedVehicle]) -> None:
    vehicle = backend(rig.vehicle.state)
    execution = ExecutionController(
        rig.authority, vehicle, ExecutionConfig(0.5, 2, 0.1), rig.clock
    )
    result = execution.start(rig.request, rig.evidence)
    expected = (
        ExecutionPhase.REJECTED
        if backend is RejectedSubmission
        else ExecutionPhase.FAILED
    )
    assert result.phase is expected
    assert len(vehicle.submissions) == 1 and vehicle.stops == ["request"]
    assert execution.start(rig.request, rig.evidence).phase is ExecutionPhase.REJECTED


def test_feedback_budget_and_history_budget(rig: Rig) -> None:
    execution = ExecutionController(
        rig.authority,
        rig.vehicle,
        ExecutionConfig(0.5, 2, 0.1, history_limit=1, feedback_budget=2),
        rig.clock,
    )
    execution.start(rig.request, rig.evidence)
    for _ in range(10):
        rig.ack(request_id="other")
    execution.update(rig.evidence)
    assert len(rig.vehicle.feedback) == 8
    execution.cancel()
    assert (
        "budget"
        in execution.start(replace(rig.request, request_id="next"), rig.evidence).detail
    )
    assert len(rig.vehicle.submissions) == 1


def test_final_check_after_release(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    original = rig.authority.release

    def revoke_after_release(request_id: str) -> object:
        result = original(request_id)
        rig.authority.revoke()
        return result

    monkeypatch.setattr(rig.authority, "release", revoke_after_release)
    result = rig.execution.start(rig.request, rig.evidence)
    assert result.phase is ExecutionPhase.REJECTED
    assert not rig.vehicle.submissions


@pytest.mark.parametrize(
    "changes",
    [
        {"acknowledgement_timeout_s": 0},
        {"completion_timeout_s": float("nan")},
        {"position_tolerance_m": -1},
        {"history_limit": 0},
        {"feedback_budget": 0},
        {"acknowledgement_timeout_s": 3},
    ],
)
def test_config_validation(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        replace(ExecutionConfig(0.5, 2, 0.1), **changes)


def test_demo(capsys: pytest.CaptureFixture[str]) -> None:
    from arwen.applications.execution_demo import main

    assert main() == 0
    output = capsys.readouterr().out
    assert output.startswith("submitted\nacknowledged\ncompleted\n")
    assert "no PX4 commands" in output


def test_deadline_crossed_during_feedback(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.execution.start(rig.request, rig.evidence)
    ack = CommandAcknowledgement(
        "request", "vehicle", "origin", 100, AckStatus.ACCEPTED
    )

    def delayed_feedback() -> CommandAcknowledgement:
        rig.clock.now = 100.5
        return ack

    monkeypatch.setattr(rig.vehicle, "acknowledgement", delayed_feedback)
    outcome = rig.execution.update(rig.evidence)
    assert outcome is not None and outcome.phase is ExecutionPhase.ACK_TIMEOUT
    assert rig.vehicle.stops == ["request"]


def test_stop_exception_is_latched(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    rig.execution.start(rig.request, rig.evidence)

    def broken_stop(request_id: str) -> StopStatus:
        raise OSError("stop failed")

    monkeypatch.setattr(rig.vehicle, "stop", broken_stop)
    result = rig.execution.cancel()
    assert result is not None and result.phase is ExecutionPhase.FAILED
    assert rig.execution.fault is not None
    assert rig.authority.revalidate(rig.request).reason is ControlReason.NO_GRANT
    assert rig.execution.cancel() is None


@pytest.mark.parametrize(
    "north,east,completed", [(1.125, 2, True), (1.125, 2.125, False)]
)
def test_euclidean_tolerance(
    rig: Rig, north: float, east: float, completed: bool
) -> None:
    execution = ExecutionController(
        rig.authority, rig.vehicle, ExecutionConfig(0.5, 2, 0.125), rig.clock
    )
    execution.start(rig.request, rig.evidence)
    rig.ack()
    rig.advance(0.1)
    rig.vehicle.state = VehicleState(
        "vehicle",
        rig.clock.now,
        rig.clock.now,
        LocalPosition(north, east, -3, "origin"),
    )
    outcome = execution.update(rig.evidence)
    assert outcome is not None
    assert (outcome.phase is ExecutionPhase.COMPLETED) is completed
