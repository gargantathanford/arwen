"""Scripted, in-memory Stage 4 execution example. No physics or flight I/O."""

from collections import deque
from dataclasses import dataclass, field

from arwen.applications.authority_demo import ScenarioClock
from arwen.config.execution import ExecutionConfig
from arwen.config.models import TimingConfig
from arwen.config.safety import SafetyConfig
from arwen.control.authority import AuthorityController
from arwen.control.execution import ExecutionController
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
class ScriptedVehicle:
    """Explicit in-memory fixture, not a firmware or physics simulation.

    Submission only records a request. The scenario must explicitly inject
    acknowledgements and observations; neither is manufactured by submit().
    """

    state: VehicleState | None
    capabilities: Capabilities = field(default_factory=lambda: Capabilities(True))
    submissions: list[PositionRequest] = field(default_factory=list)
    stops: list[str] = field(default_factory=list)
    feedback: deque[CommandAcknowledgement] = field(default_factory=deque)

    def snapshot(self) -> VehicleState | None:
        return self.state

    def submit(self, request: PositionRequest) -> RequestResult:
        self.submissions.append(request)
        return RequestResult(request.request_id, RequestStatus.ACCEPTED)

    def acknowledgement(self) -> CommandAcknowledgement | None:
        return self.feedback.popleft() if self.feedback else None

    def stop(self, request_id: str) -> StopStatus:
        self.stops.append(request_id)
        return StopStatus.STOPPED


def main() -> int:
    clock = ScenarioClock()
    initial = LocalPosition(0, 0, 0, "synthetic-origin")
    vehicle = ScriptedVehicle(VehicleState("synthetic", clock.now, clock.now, initial))
    authority = AuthorityController(SafetyConfig(TimingConfig(1, 2), 1, 5), clock)
    evidence = AuthorityEvidence(
        "synthetic", clock.now, True, AuthorityMode.AUTONOMY_PERMITTED
    )
    authority.update(vehicle.state, evidence)
    grant = authority.grant("synthetic-application", 2)
    execution = ExecutionController(
        authority, vehicle, ExecutionConfig(0.5, 1.5, 0.1), clock
    )
    target = LocalPosition(1, 2, -3, initial.origin_id)
    request = PositionRequest(
        "example", grant.authority_id, target, clock.now, clock.now + 2
    )
    try:
        print(execution.start(request, evidence).phase.value)
        vehicle.feedback.append(
            CommandAcknowledgement(
                request.request_id,
                "synthetic",
                initial.origin_id,
                clock.now,
                AckStatus.ACCEPTED,
            )
        )
        outcome = execution.update(evidence)
        if outcome is not None:
            print(outcome.phase.value)
        clock.now += 0.1
        vehicle.state = VehicleState("synthetic", clock.now, clock.now, target)
        evidence = AuthorityEvidence(
            "synthetic", clock.now, True, AuthorityMode.AUTONOMY_PERMITTED
        )
        outcome = execution.update(evidence)
        if outcome is not None:
            print(outcome.phase.value)
    finally:
        execution.close()
    print("Scripted local execution only; no PX4 commands or physical movement.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
