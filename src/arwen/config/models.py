"""Explicit application configuration; no environment reads on import."""

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True, slots=True)
class TimingConfig:
    """Application-selected freshness and request lifetime limits, in seconds."""

    max_state_age_s: float
    request_ttl_s: float

    def __post_init__(self) -> None:
        for value in (self.max_state_age_s, self.request_ttl_s):
            if not isfinite(value) or value <= 0:
                raise ValueError("timing limits must be finite and positive")
