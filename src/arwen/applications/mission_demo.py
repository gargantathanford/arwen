"""Scripted Stage 5 observation-to-intent-to-execution example, without I/O."""

from arwen.applications.authority_demo import ScenarioClock
from arwen.applications.execution_demo import ScriptedVehicle
from arwen.applications.observed_target import ObservedTargetApplication
from arwen.config.execution import ExecutionConfig
from arwen.config.missions import ObservedTargetConfig
from arwen.config.models import TimingConfig
from arwen.config.safety import SafetyConfig
from arwen.control.authority import AuthorityController
from arwen.missions.observed_target import ObservedTargetMission
from arwen.perception.observations import TargetObservation
from arwen.vehicle.authority import AuthorityEvidence, AuthorityMode
from arwen.vehicle.contracts import LocalPosition, VehicleState
from arwen.vehicle.execution import AckStatus, CommandAcknowledgement


def main() -> int:
    clock = ScenarioClock()
    origin = "synthetic-origin"
    vehicle = ScriptedVehicle(
        VehicleState("synthetic", clock.now, clock.now, LocalPosition(0, 0, 0, origin))
    )
    authority = AuthorityController(SafetyConfig(TimingConfig(1, 2), 1, 5), clock)
    evidence = AuthorityEvidence(
        "synthetic", clock.now, True, AuthorityMode.AUTONOMY_PERMITTED
    )
    authority.update(vehicle.state, evidence)
    grant = authority.grant("synthetic-mission", 2)
    mission = ObservedTargetMission(
        ObservedTargetConfig(
            "observed-target",
            "synthetic",
            "synthetic-sensor",
            "marker",
            1,
            0.5,
            0.9,
            0.2,
            0.2,
            0.5,
        )
    )
    application = ObservedTargetApplication(
        mission, authority, grant, vehicle, ExecutionConfig(0.5, 1.5, 0.1), 2, clock
    )
    target = LocalPosition(1, 2, -3, origin)
    observation = TargetObservation(
        "synthetic-sensor", "marker", clock.now, clock.now, target, 0.99, 0.05
    )
    try:
        print(application.tick(observation, evidence).mission.phase.value)
        request = vehicle.submissions[0]
        vehicle.feedback.append(
            CommandAcknowledgement(
                request.request_id, "synthetic", origin, clock.now, AckStatus.ACCEPTED
            )
        )
        application.tick(observation, evidence)
        clock.now += 0.1
        vehicle.state = VehicleState("synthetic", clock.now, clock.now, target)
        observation = TargetObservation(
            "synthetic-sensor", "marker", clock.now, clock.now, target, 0.99, 0.05
        )
        evidence = AuthorityEvidence(
            "synthetic", clock.now, True, AuthorityMode.AUTONOMY_PERMITTED
        )
        result = application.tick(observation, evidence)
        print(result.mission.phase.value)
        print(result.mission.reason.value)
    finally:
        application.close()
    print("Synthetic observations and vehicle only; no camera or PX4 commands.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
