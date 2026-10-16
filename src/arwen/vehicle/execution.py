"""Optional execution feedback boundary, independent of firmware protocols."""

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Protocol

from arwen.vehicle.contracts import VehicleAdapter


class AckStatus(Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class CommandAcknowledgement:
    """A normalized acknowledgement, not a submission or physical completion.

    observed_at is the host monotonic receipt time; adapters must correlate wire
    feedback with the original request and estimator session without guessing.
    """

    request_id: str
    source_id: str
    origin_id: str
    observed_at: float
    status: AckStatus

    def __post_init__(self) -> None:
        if not all(
            v.strip() for v in (self.request_id, self.source_id, self.origin_id)
        ):
            raise ValueError("acknowledgement identifiers must be nonempty")
        if not isfinite(self.observed_at) or self.observed_at < 0:
            raise ValueError("acknowledgement time must be finite and nonnegative")
        if not isinstance(self.status, AckStatus):
            raise ValueError("explicit acknowledgement status required")


class StopStatus(Enum):
    STOPPED = "future_local_output_stopped"
    UNCONFIRMED = "stop_unconfirmed"


class ExecutionAdapter(VehicleAdapter, Protocol):
    """Single-owner nonblocking execution extension, with no implicit retries."""

    def acknowledgement(self) -> CommandAcknowledgement | None:
        """Return at most one pending event without blocking."""
        ...

    def stop(self, request_id: str) -> StopStatus:
        """Stop future local output; never claim reversal of physical effects."""
        ...
