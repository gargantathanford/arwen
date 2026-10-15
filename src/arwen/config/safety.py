"""Explicit local policy bounds; applications select their operating limits."""

from dataclasses import dataclass
from math import isfinite

from arwen.config.models import TimingConfig


@dataclass(frozen=True, slots=True)
class SafetyConfig:
    timing: TimingConfig
    max_authority_age_s: float
    max_grant_lifetime_s: float
    pending_limit: int = 32
    request_id_budget: int = 4096

    def __post_init__(self) -> None:
        for duration in (self.max_authority_age_s, self.max_grant_lifetime_s):
            if not isfinite(duration) or duration <= 0:
                raise ValueError("authority bounds must be finite and positive")
        for count in (self.pending_limit, self.request_id_budget):
            if type(count) is not int or count <= 0:
                raise ValueError("queue and replay bounds must be positive integers")
        if self.pending_limit > self.request_id_budget:
            raise ValueError("pending limit cannot exceed replay budget")
