"""Deterministic Stage 5 mission and composition scenarios without hardware."""

from dataclasses import FrozenInstanceError, dataclass, replace

import pytest

from arwen.applications.authority_demo import ScenarioClock
from arwen.applications.execution_demo import ScriptedVehicle
from arwen.applications.observed_target import ObservedTargetApplication
from arwen.config.execution import ExecutionConfig
from arwen.config.missions import ObservedTargetConfig
from arwen.config.models import TimingConfig
from arwen.config.safety import SafetyConfig
from arwen.control.authority import AuthorityController, RequestDecision
from arwen.control.execution import ExecutionController, ExecutionPhase
from arwen.missions.observed_target import (
    MissionPhase,
    MissionReason,
    ObservedTargetMission,
)
from arwen.perception.observations import TargetObservation
from arwen.vehicle.authority import AuthorityEvidence, AuthorityMode
from arwen.vehicle.contracts import (
    Capabilities,
    LocalPosition,
    PositionRequest,
    VehicleState,
)
from arwen.vehicle.execution import AckStatus, CommandAcknowledgement


def config() -> ObservedTargetConfig:
    return ObservedTargetConfig(
        "mission", "vehicle", "sensor", "marker", 1, 0.5, 0.9, 0.25, 0.25, 0.25
    )


def state(now: float = 100, target: bool = False) -> VehicleState:
    position = (
        LocalPosition(1, 2, -3, "origin")
        if target
        else LocalPosition(0, 0, 0, "origin")
    )
    return VehicleState("vehicle", now, now, position)


def observation(now: float = 100) -> TargetObservation:
    return TargetObservation(
        "sensor", "marker", now, now, LocalPosition(1, 2, -3, "origin"), 0.9, 0.25
    )


def ready() -> ObservedTargetMission:
    mission = ObservedTargetMission(config())
    assert mission.evaluate(state(), observation(), 100).phase is MissionPhase.READY
    return mission


def executing() -> ObservedTargetMission:
    mission = ready()
    intent = mission.snapshot.intent
    assert intent is not None
    mission.dispatched(intent.intent_id)
    return mission


def test_intent_deadline_and_zero_position() -> None:
    mission = ObservedTargetMission(config())
    obs = replace(observation(), position=LocalPosition(0, 0, 0, "origin"))
    snapshot = mission.evaluate(state(), obs, 100)
    assert snapshot.phase is MissionPhase.READY and snapshot.intent is not None
    assert snapshot.intent.target == obs.position
    assert snapshot.intent.admit_before == 100.25
    assert not hasattr(snapshot.intent, "authority_id")
    with pytest.raises(FrozenInstanceError):
        obs.confidence = 0


@pytest.mark.parametrize(
    "case,reason",
    [
        ("state", MissionReason.WAITING_STATE),
        ("observation", MissionReason.WAITING_OBSERVATION),
        ("stale_state", MissionReason.STALE_STATE),
        ("stale_observation", MissionReason.STALE_OBSERVATION),
        ("source", MissionReason.WRONG_SOURCE),
        ("sensor", MissionReason.WRONG_OBSERVATION),
        ("target", MissionReason.WRONG_OBSERVATION),
        ("position", MissionReason.UNKNOWN_POSITION),
        ("frame", MissionReason.FRAME_MISMATCH),
        ("confidence", MissionReason.LOW_CONFIDENCE),
        ("uncertainty", MissionReason.UNCERTAIN_POSITION),
        ("future", MissionReason.STALE_OBSERVATION),
    ],
)
def test_wait_before_selection_abort_after_selection(
    case: str, reason: MissionReason
) -> None:
    current_state: VehicleState | None = state()
    obs: TargetObservation | None = observation()
    if case == "state":
        current_state = None
    elif case == "observation":
        obs = None
    elif case == "stale_state":
        current_state = replace(state(), measured_at=99)
    elif case == "stale_observation":
        obs = replace(observation(), measured_at=99.5)
    elif case == "source":
        current_state = replace(state(), source_id="other")
    elif case == "sensor":
        obs = replace(observation(), sensor_id="other")
    elif case == "target":
        obs = replace(observation(), target_id="other")
    elif case == "position":
        obs = replace(observation(), position=None)
    elif case == "frame":
        obs = replace(observation(), position=LocalPosition(1, 2, -3, "other"))
    elif case == "confidence":
        obs = replace(observation(), confidence=0.89)
    elif case == "uncertainty":
        obs = replace(observation(), uncertainty_m=0.251)
    elif case == "future":
        obs = replace(observation(), received_at=101)
    waiting = ObservedTargetMission(config()).evaluate(current_state, obs, 100)
    assert waiting.phase is MissionPhase.WAITING and waiting.reason is reason
    active = executing().evaluate(current_state, obs, 100)
    assert active.phase is MissionPhase.ABORTED and active.reason is reason


def test_waiting_can_recover_but_terminal_cannot() -> None:
    mission = ObservedTargetMission(config())
    mission.evaluate(None, None, 100)
    assert mission.evaluate(state(), observation(), 100).phase is MissionPhase.READY
    mission.abort()
    final = mission.snapshot
    assert mission.evaluate(state(100.1), observation(100.1), 100.1) == final
    with pytest.raises(RuntimeError):
        mission.dispatched("other")


def test_duplicate_does_not_refresh_measurement_age() -> None:
    mission = executing()
    obs = replace(observation(), received_at=100.49)
    assert mission.evaluate(state(100.49), obs, 100.49).phase is MissionPhase.EXECUTING
    snapshot = mission.evaluate(state(100.5), replace(obs, received_at=100.5), 100.5)
    assert snapshot.phase is MissionPhase.ABORTED
    assert snapshot.reason is MissionReason.STALE_OBSERVATION


def test_fresh_samples_do_not_bridge_sensor_gap() -> None:
    snapshot = executing().evaluate(state(100.5), observation(100.5), 100.5)
    assert snapshot.reason is MissionReason.STALE_OBSERVATION


def test_regression_and_conflicting_measurement() -> None:
    for changes, reason in (
        ({"measured_at": 99.9}, MissionReason.REGRESSING_DATA),
        (
            {"position": LocalPosition(1.01, 2, -3, "origin")},
            MissionReason.CONFLICTING_SAMPLE,
        ),
        ({"confidence": 0.95}, MissionReason.CONFLICTING_SAMPLE),
    ):
        snapshot = executing().evaluate(state(), replace(observation(), **changes), 100)
        assert snapshot.reason is reason
    snapshot = executing().evaluate(
        replace(state(), measured_at=99.9), observation(), 100
    )
    assert snapshot.reason is MissionReason.REGRESSING_DATA


@pytest.mark.parametrize(
    "shift,expected", [(0.25, MissionPhase.EXECUTING), (0.251, MissionPhase.ABORTED)]
)
def test_frozen_target_and_motion_limit(shift: float, expected: MissionPhase) -> None:
    mission = executing()
    initial = mission.snapshot.intent
    obs = replace(
        observation(100.1), position=LocalPosition(1 + shift, 2, -3, "origin")
    )
    result = mission.evaluate(state(100.1), obs, 100.1)
    assert result.phase is expected
    assert result.intent == initial


def test_session_reset_aborts() -> None:
    snapshot = executing().evaluate(
        VehicleState("vehicle", 100.1, 100.1, LocalPosition(0, 0, 0, "new")),
        observation(100.1),
        100.1,
    )
    assert snapshot.reason is MissionReason.FRAME_MISMATCH


def test_intent_expiry_and_correlated_completion() -> None:
    mission = ready()
    assert (
        mission.evaluate(state(100.25), observation(100.25), 100.25).reason
        is MissionReason.INTENT_EXPIRED
    )
    mission = executing()
    intent = mission.snapshot.intent
    assert intent is not None
    with pytest.raises(RuntimeError):
        mission.finished("wrong", True)
    assert mission.finished(intent.intent_id, True).phase is MissionPhase.SUCCEEDED
    assert mission.abort().phase is MissionPhase.SUCCEEDED


@pytest.mark.parametrize("now", [99, -1, float("nan"), float("inf")])
def test_bad_clock_aborts(now: float) -> None:
    mission = executing()
    with pytest.raises(ValueError):
        mission.evaluate(state(), observation(), now)
    assert mission.snapshot.reason is MissionReason.CLOCK_ERROR


@pytest.mark.parametrize(
    "changes",
    [
        {"sensor_id": ""},
        {"target_id": " "},
        {"measured_at": -1},
        {"received_at": 99},
        {"measured_at": float("nan")},
        {"confidence": -0.1},
        {"confidence": 1.1},
        {"confidence": float("nan")},
        {"uncertainty_m": -1},
        {"uncertainty_m": float("inf")},
    ],
)
def test_observation_validation(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        replace(observation(), **changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"mission_id": ""},
        {"max_state_age_s": 0},
        {"max_observation_age_s": float("nan")},
        {"min_confidence": 2},
        {"max_uncertainty_m": -1},
        {"max_target_shift_m": -1},
        {"intent_lifetime_s": float("inf")},
    ],
)
def test_mission_config_validation(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        replace(config(), **changes)


@dataclass
class Rig:
    clock: ScenarioClock
    vehicle: ScriptedVehicle
    authority: AuthorityController
    evidence: AuthorityEvidence
    mission: ObservedTargetMission
    application: ObservedTargetApplication

    def advance(self, target: bool = False) -> None:
        self.clock.now += 0.1
        self.vehicle.state = state(self.clock.now, target)
        self.evidence = replace(self.evidence, observed_at=self.clock.now)

    def ack(self, status: AckStatus = AckStatus.ACCEPTED) -> None:
        request = self.vehicle.submissions[-1]
        self.vehicle.feedback.append(
            CommandAcknowledgement(
                request.request_id, "vehicle", "origin", self.clock.now, status
            )
        )


@pytest.fixture
def rig() -> Rig:
    clock = ScenarioClock()
    vehicle = ScriptedVehicle(state())
    authority = AuthorityController(SafetyConfig(TimingConfig(1, 2), 1, 5), clock)
    evidence = AuthorityEvidence("vehicle", 100, True, AuthorityMode.AUTONOMY_PERMITTED)
    authority.update(vehicle.state, evidence)
    grant = authority.grant("mission", 3)
    mission = ObservedTargetMission(config())
    application = ObservedTargetApplication(
        mission, authority, grant, vehicle, ExecutionConfig(0.5, 1.5, 0.1), 2, clock
    )
    return Rig(clock, vehicle, authority, evidence, mission, application)


def test_full_observation_to_success_pipeline(rig: Rig) -> None:
    started = rig.application.tick(observation(), rig.evidence)
    assert started.mission.phase is MissionPhase.EXECUTING
    assert len(rig.vehicle.submissions) == 1
    assert rig.vehicle.submissions[0].target == observation().position
    rig.ack()
    assert (
        rig.application.tick(observation(), rig.evidence).mission.phase
        is MissionPhase.EXECUTING
    )
    rig.advance(target=True)
    final = rig.application.tick(observation(rig.clock.now), rig.evidence)
    assert final.mission.phase is MissionPhase.SUCCEEDED
    assert (
        final.execution is not None
        and final.execution.phase is ExecutionPhase.COMPLETED
    )
    assert len(rig.vehicle.stops) == 1
    assert rig.application.tick(None, None) == final
    assert rig.application.close() == final
    assert len(rig.vehicle.submissions) == 1


@pytest.mark.parametrize(
    "case",
    ["sensor_loss", "takeover", "failsafe", "disconnect", "sensor_shift", "clock"],
)
def test_interruption_cancels_and_never_restarts(rig: Rig, case: str) -> None:
    rig.application.tick(observation(), rig.evidence)
    rig.advance()
    obs: TargetObservation | None = observation(rig.clock.now)
    evidence = rig.evidence
    if case == "sensor_loss":
        obs = None
    elif case == "takeover":
        evidence = replace(evidence, mode=AuthorityMode.OPERATOR)
    elif case == "failsafe":
        evidence = replace(evidence, mode=AuthorityMode.FAILSAFE)
    elif case == "disconnect":
        evidence = replace(evidence, link_healthy=False)
    elif case == "sensor_shift":
        obs = replace(obs, position=LocalPosition(5, 2, -3, "origin"))
    else:
        rig.clock.now = float("nan")
    final = rig.application.tick(obs, evidence)
    assert final.mission.phase is MissionPhase.ABORTED
    assert rig.vehicle.stops and len(rig.vehicle.submissions) == 1
    rig.clock.now = 100.2
    assert (
        rig.application.tick(observation(100.2), rig.evidence).mission.phase
        is MissionPhase.ABORTED
    )
    assert len(rig.vehicle.submissions) == 1


def test_rejection_and_shutdown(rig: Rig) -> None:
    rig.application.tick(observation(), rig.evidence)
    rig.ack(AckStatus.REJECTED)
    assert (
        rig.application.tick(observation(), rig.evidence).mission.phase
        is MissionPhase.ABORTED
    )
    rig.application.close()
    assert len(rig.vehicle.stops) == 1


def test_no_sensor_waits_without_submission(rig: Rig) -> None:
    assert (
        rig.application.tick(None, rig.evidence).mission.phase is MissionPhase.WAITING
    )
    assert not rig.vehicle.submissions
    assert rig.application.close().mission.reason is MissionReason.CANCELLED
    assert rig.application.close().mission.reason is MissionReason.CANCELLED


def test_unsupported_adapter_aborts_without_submit(rig: Rig) -> None:
    rig.vehicle.capabilities = Capabilities(False)
    assert (
        rig.application.tick(observation(), rig.evidence).mission.phase
        is MissionPhase.ABORTED
    )
    assert not rig.vehicle.submissions


def test_admission_deadline_rechecked_at_submit(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    grant = rig.authority.grant  # Existing active lease cannot be silently replaced.
    with pytest.raises(RuntimeError):
        grant("other", 1)
    original = rig.authority.revalidate

    def delayed_check(request: PositionRequest) -> RequestDecision:
        decision = original(request)
        rig.clock.now = 100.25
        return decision

    monkeypatch.setattr(rig.authority, "revalidate", delayed_check)
    final = rig.application.tick(observation(), rig.evidence)
    assert final.mission.phase is MissionPhase.ABORTED
    assert final.execution is not None and "admission" in final.execution.detail
    assert not rig.vehicle.submissions


def test_invalid_admission_deadline_and_existing_callers(rig: Rig) -> None:
    # Public Stage 4 API preserves default behavior; invalid explicit limits deny.
    request = PositionRequest(
        "x", "unknown", LocalPosition(0, 0, 0, "origin"), 100, 101
    )
    execution = ExecutionController(
        rig.authority, rig.vehicle, ExecutionConfig(0.5, 1.5, 0.1), rig.clock
    )
    assert (
        execution.start(request, rig.evidence, admit_before=float("nan")).phase
        is ExecutionPhase.REJECTED
    )
    assert not rig.vehicle.submissions


def test_demo(capsys: pytest.CaptureFixture[str]) -> None:
    from arwen.applications.mission_demo import main

    assert main() == 0
    output = capsys.readouterr().out
    assert output.startswith("executing\nsucceeded\nexecution_completed\n")
    assert "no camera or PX4 commands" in output
