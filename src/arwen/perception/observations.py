"""Detector-neutral observations already localized in a named local NED frame."""

from dataclasses import dataclass
from math import isfinite

from arwen.vehicle.contracts import LocalPosition


@dataclass(frozen=True, slots=True)
class TargetObservation:
    """One target measurement; receipt never refreshes its measurement age.

    Host monotonic seconds share VehicleState's clock domain. uncertainty_m is a
    producer-declared conservative positional error bound, not a covariance or
    guaranteed statistical confidence. confidence is a producer-calibrated [0,1]
    quality score. Producers must document those meanings for real integrations.
    Missing position is explicitly unknown, not an implicit zero or lost-target
    prediction. No frame transformation is performed by this contract.
    """

    sensor_id: str
    target_id: str
    measured_at: float
    received_at: float
    position: LocalPosition | None
    confidence: float
    uncertainty_m: float

    def __post_init__(self) -> None:
        if not self.sensor_id.strip() or not self.target_id.strip():
            raise ValueError("sensor and target identities must be nonempty")
        if (
            not all(
                isfinite(t) and t >= 0 for t in (self.measured_at, self.received_at)
            )
            or self.received_at < self.measured_at
        ):
            raise ValueError(
                "observation timestamps must be finite, nonnegative and ordered"
            )
        if not isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be finite and within [0,1]")
        if not isfinite(self.uncertainty_m) or self.uncertainty_m < 0:
            raise ValueError("uncertainty must be finite and nonnegative")
