"""Deterministic Stage 3 scenario: python -m arwen.applications.authority_demo.

Synthetic inputs only. No adapters, sockets or aircraft are accessed.
"""

from dataclasses import dataclass

from arwen.config.models import TimingConfig
from arwen.config.safety import SafetyConfig
from arwen.control.authority import AuthorityController
from arwen.vehicle.authority import AuthorityEvidence, AuthorityMode
from arwen.vehicle.contracts import LocalPosition, PositionRequest, VehicleState


@dataclass
class ScenarioClock:
    now: float = 100.0

    def __call__(self) -> float:
        return self.now


def main() -> int:
    clock = ScenarioClock()
    controller = AuthorityController(SafetyConfig(TimingConfig(1, 2), 1, 5), clock)
    position = LocalPosition(0, 0, 0, "synthetic-estimator")
    state = VehicleState("synthetic-vehicle", clock.now, clock.now, position)
    controller.update(
        state,
        AuthorityEvidence(
            state.source_id, clock.now, True, AuthorityMode.AUTONOMY_PERMITTED
        ),
    )
    grant = controller.grant("synthetic-mission", 2)
    request = PositionRequest("example", grant.authority_id, position, clock.now, 101)
    print(f"synthetic request: {controller.queue(request).reason.value}")
    clock.now += 0.1
    results = controller.update(
        state,
        AuthorityEvidence(state.source_id, clock.now, True, AuthorityMode.OPERATOR),
    )
    for result in results:
        reason = (
            result.safety_reason.value if result.safety_reason else result.reason.value
        )
        print(f"{result.request_id}: {reason}; pending request cancelled")
    print(f"release after takeover: {controller.release('example').reason.value}")
    controller.close()
    print("Local policy scenario finished; no vehicle commands were sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
