"""Pure local position-request policy; never performs I/O or grants authority."""

from enum import Enum
from math import isfinite

from arwen.config.safety import SafetyConfig
from arwen.vehicle.authority import AuthorityEvidence, AuthorityMode
from arwen.vehicle.contracts import PositionRequest, VehicleState


class SafetyReason(Enum):
    ALLOWED = "allowed"
    MISSING_EVIDENCE = "missing_evidence"
    DISCONNECTED = "disconnected"
    UNKNOWN_AUTHORITY = "unknown_authority"
    OPERATOR_TAKEOVER = "operator_takeover"
    FAILSAFE = "failsafe"
    STALE_AUTHORITY = "stale_authority"
    MISSING_POSITION = "missing_position"
    STALE_POSITION = "stale_position"
    SOURCE_MISMATCH = "source_mismatch"
    FRAME_MISMATCH = "frame_mismatch"
    FUTURE_REQUEST = "future_request"
    EXPIRED_REQUEST = "expired_request"
    EXCESSIVE_LIFETIME = "excessive_lifetime"


class PositionSafetyPolicy:
    def __init__(self, config: SafetyConfig) -> None:
        self.config = config

    def readiness(
        self, state: VehicleState | None, evidence: AuthorityEvidence | None, now: float
    ) -> SafetyReason:
        if not isfinite(now) or now < 0:
            raise ValueError("now must be finite and nonnegative")
        if evidence is None:
            return SafetyReason.MISSING_EVIDENCE
        if not evidence.link_healthy:
            return SafetyReason.DISCONNECTED
        if evidence.mode is AuthorityMode.OPERATOR:
            return SafetyReason.OPERATOR_TAKEOVER
        if evidence.mode is AuthorityMode.FAILSAFE:
            return SafetyReason.FAILSAFE
        if evidence.mode is not AuthorityMode.AUTONOMY_PERMITTED:
            return SafetyReason.UNKNOWN_AUTHORITY
        if not 0 <= now - evidence.observed_at < self.config.max_authority_age_s:
            return SafetyReason.STALE_AUTHORITY
        if state is None or state.position is None:
            return SafetyReason.MISSING_POSITION
        if evidence.source_id != state.source_id:
            return SafetyReason.SOURCE_MISMATCH
        if not state.position_is_fresh(now, self.config.timing.max_state_age_s):
            return SafetyReason.STALE_POSITION
        return SafetyReason.ALLOWED

    def evaluate(
        self,
        request: PositionRequest,
        state: VehicleState | None,
        evidence: AuthorityEvidence | None,
        now: float,
    ) -> SafetyReason:
        reason = self.readiness(state, evidence, now)
        if reason is not SafetyReason.ALLOWED:
            return reason
        if request.issued_at > now:
            return SafetyReason.FUTURE_REQUEST
        if now >= request.expires_at:
            return SafetyReason.EXPIRED_REQUEST
        if request.expires_at - request.issued_at > self.config.timing.request_ttl_s:
            return SafetyReason.EXCESSIVE_LIFETIME
        if state is None or state.position is None:
            return SafetyReason.MISSING_POSITION
        if request.target.origin_id != state.position.origin_id:
            return SafetyReason.FRAME_MISMATCH
        return SafetyReason.ALLOWED
