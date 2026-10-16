"""Application-selected execution limits, in host seconds and SI metres."""

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    acknowledgement_timeout_s: float
    completion_timeout_s: float
    position_tolerance_m: float
    history_limit: int = 1024
    feedback_budget: int = 64

    def __post_init__(self) -> None:
        for value in (
            self.acknowledgement_timeout_s,
            self.completion_timeout_s,
            self.position_tolerance_m,
        ):
            if not isfinite(value) or value <= 0:
                raise ValueError(
                    "execution timing and tolerance must be finite and positive"
                )
        if self.acknowledgement_timeout_s > self.completion_timeout_s:
            raise ValueError(
                "acknowledgement deadline must not exceed completion deadline"
            )
        for count in (self.history_limit, self.feedback_budget):
            if type(count) is not int or count <= 0:
                raise ValueError("execution resource bounds must be positive integers")
