"""Firmware-neutral contracts for requesting a local position.

All times use one injected monotonic clock domain, in seconds. Positions use
metres in a local north/east/down frame identified by origin_id. These contracts
do not grant flight authority or establish that a physical action occurred.
"""

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Protocol


@dataclass(frozen=True, slots=True)
class LocalPosition:
    """A finite NED position relative to a named, application-defined origin."""

    north_m: float
    east_m: float
    down_m: float
    origin_id: str

    def __post_init__(self) -> None:
        if not self.origin_id.strip():
            raise ValueError("origin_id must be nonempty")
        if not all(isfinite(v) for v in (self.north_m, self.east_m, self.down_m)):
            raise ValueError("position coordinates must be finite")


@dataclass(frozen=True, slots=True)
class VehicleState:
    """Unknown position is None; receipt time never refreshes measurement age."""

    source_id: str
    measured_at: float
    received_at: float
    position: LocalPosition | None

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("source_id must be nonempty")
        if (
            not all(
                isfinite(t) and t >= 0 for t in (self.measured_at, self.received_at)
            )
            or self.received_at < self.measured_at
        ):
            raise ValueError("timestamps must be finite, nonnegative and ordered")

    def position_is_fresh(self, now: float, max_age_s: float) -> bool:
        """Reject unknown position, future timestamps and age at the limit."""
        if not isfinite(now) or now < 0:
            raise ValueError("now must be finite and nonnegative")
        if not isfinite(max_age_s) or max_age_s <= 0:
            raise ValueError("max_age_s must be finite and positive")
        return (
            self.position is not None
            and self.received_at <= now
            and 0 <= now - self.measured_at < max_age_s
        )


@dataclass(frozen=True, slots=True)
class Capabilities:
    """Explicit adapter support; no support is assumed by default."""

    local_position_requests: bool = False


@dataclass(frozen=True, slots=True)
class PositionRequest:
    """One request with a correlation ID and exclusive expiration deadline.

    authority_id names an application-issued authority grant. Adapters must
    validate it; merely supplying a string must never confer authority.
    """

    request_id: str
    authority_id: str
    target: LocalPosition
    issued_at: float
    expires_at: float

    def __post_init__(self) -> None:
        if not self.request_id.strip() or not self.authority_id.strip():
            raise ValueError("request and authority IDs must be nonempty")
        if (
            not all(isfinite(t) and t >= 0 for t in (self.issued_at, self.expires_at))
            or self.expires_at <= self.issued_at
        ):
            raise ValueError("expiration must follow finite nonnegative issue time")


class RequestStatus(Enum):
    """Submission outcomes; ACCEPTED is not observed physical completion."""

    ACCEPTED = "accepted"
    UNSUPPORTED = "unsupported"
    EXPIRED = "expired"
    INVALID_TIME = "invalid_time"
    NO_AUTHORITY = "no_authority"
    UNAVAILABLE_STATE = "unavailable_state"
    FRAME_MISMATCH = "frame_mismatch"
    DUPLICATE = "duplicate"


@dataclass(frozen=True, slots=True)
class RequestResult:
    """Adapter submission result correlated with the original request."""

    request_id: str
    status: RequestStatus

    def __post_init__(self) -> None:
        if not self.request_id.strip():
            raise ValueError("request_id must be nonempty")


class VehicleAdapter(Protocol):
    """Minimal synchronous boundary; submission must not block on completion.

    Implementations enforce capabilities, authority, freshness, frame agreement,
    expiry and replay rejection. Transport lifecycle remains a later contract.
    """

    @property
    def capabilities(self) -> Capabilities: ...

    def snapshot(self) -> VehicleState | None: ...

    def submit(self, request: PositionRequest) -> RequestResult: ...
