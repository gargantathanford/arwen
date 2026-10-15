"""Firmware-neutral authority evidence; absence never grants autonomy."""

from dataclasses import dataclass
from enum import Enum
from math import isfinite


class AuthorityMode(Enum):
    UNKNOWN = "unknown"
    AUTONOMY_PERMITTED = "autonomy_permitted"
    OPERATOR = "operator"
    FAILSAFE = "failsafe"


@dataclass(frozen=True, slots=True)
class AuthorityEvidence:
    """Trusted application input, not inferred from a heartbeat or armed bit.

    observed_at uses the same host monotonic clock as VehicleState. The producer
    must update timestamps only for newly established evidence, not cached reads.
    Source IDs are routing context, not authenticated identities.
    """

    source_id: str
    observed_at: float
    link_healthy: bool
    mode: AuthorityMode

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("source_id must be nonempty")
        if not isfinite(self.observed_at) or self.observed_at < 0:
            raise ValueError("observation time must be finite and nonnegative")
        if type(self.link_healthy) is not bool or not isinstance(
            self.mode, AuthorityMode
        ):
            raise ValueError("link health and authority mode must be explicit")
