"""Application-specific observed-target policy thresholds; no universal defaults."""

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True, slots=True)
class ObservedTargetConfig:
    mission_id: str
    vehicle_source_id: str
    sensor_id: str
    target_id: str
    max_state_age_s: float
    max_observation_age_s: float
    min_confidence: float
    max_uncertainty_m: float
    max_target_shift_m: float
    intent_lifetime_s: float

    def __post_init__(self) -> None:
        if not all(
            v.strip()
            for v in (
                self.mission_id,
                self.vehicle_source_id,
                self.sensor_id,
                self.target_id,
            )
        ):
            raise ValueError(
                "mission, vehicle, sensor and target identities are required"
            )
        for value in (
            self.max_state_age_s,
            self.max_observation_age_s,
            self.intent_lifetime_s,
        ):
            if not isfinite(value) or value <= 0:
                raise ValueError("age and intent limits must be finite and positive")
        if not isfinite(self.min_confidence) or not 0 <= self.min_confidence <= 1:
            raise ValueError("confidence threshold must be finite and within [0,1]")
        for value in (self.max_uncertainty_m, self.max_target_shift_m):
            if not isfinite(value) or value < 0:
                raise ValueError("spatial bounds must be finite and nonnegative")
