"""Single-owner authority leases and cancellable local request admission.

No adapter, transport or flight command is invoked. Call update regularly with
fresh trusted evidence; release is a one-shot policy decision, not dispatch.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from math import isfinite
from uuid import uuid4

from arwen.config.safety import SafetyConfig
from arwen.safety.policy import PositionSafetyPolicy, SafetyReason
from arwen.vehicle.authority import AuthorityEvidence
from arwen.vehicle.contracts import PositionRequest, VehicleState


class ControlReason(Enum):
    QUEUED = "queued"
    AUTHORIZED = "authorized_locally"
    CANCELLED = "cancelled"
    REVOKED = "revoked"
    GRANT_EXPIRED = "grant_expired"
    NO_GRANT = "no_grant"
    DUPLICATE = "duplicate"
    UNKNOWN_REQUEST = "unknown_request"
    CAPACITY = "capacity"
    ID_BUDGET = "id_budget_exhausted"
    SAFETY_DENIED = "safety_denied"
    SESSION_CHANGED = "session_changed"
    REGRESSING_EVIDENCE = "regressing_evidence"
    CLOCK_ERROR = "clock_error"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class RequestDecision:
    request_id: str
    reason: ControlReason
    safety_reason: SafetyReason | None = None


@dataclass(frozen=True, slots=True)
class AuthorityGrant:
    """Controller-issued lease. Supplying a matching string alone is insufficient."""

    authority_id: str
    owner_id: str
    source_id: str
    origin_id: str
    issued_at: float
    expires_at: float


class AuthorityController:
    def __init__(self, config: SafetyConfig, clock: Callable[[], float]) -> None:
        self._config = config
        self._policy = PositionSafetyPolicy(config)
        self._clock = clock
        self._last_now = 0.0
        self._state: VehicleState | None = None
        self._evidence: AuthorityEvidence | None = None
        self._grant: AuthorityGrant | None = None
        self._pending: dict[str, PositionRequest] = {}
        self._released: dict[str, PositionRequest] = {}
        self._decisions: dict[str, RequestDecision] = {}
        self._revoked_at = -1.0
        self._closed = False

    def _now(self) -> float:
        now = self._clock()
        if not isfinite(now) or now < self._last_now:
            self._invalidate(ControlReason.CLOCK_ERROR, self._last_now)
            raise ValueError("clock must be finite, nonnegative and monotonic")
        self._last_now = now
        return now

    def _invalidate(
        self, reason: ControlReason, now: float, safety: SafetyReason | None = None
    ) -> tuple[RequestDecision, ...]:
        self._grant = None
        self._released.clear()
        self._revoked_at = max(self._revoked_at, now)
        decisions = tuple(RequestDecision(key, reason, safety) for key in self._pending)
        self._pending.clear()
        self._decisions.update((result.request_id, result) for result in decisions)
        return decisions

    def _check(self, now: float) -> tuple[RequestDecision, ...]:
        grant = self._grant
        if grant is None:
            return ()
        if now >= grant.expires_at:
            return self._invalidate(ControlReason.GRANT_EXPIRED, now)
        reason = self._policy.readiness(self._state, self._evidence, now)
        if reason is not SafetyReason.ALLOWED:
            return self._invalidate(ControlReason.SAFETY_DENIED, now, reason)
        state = self._state
        if (
            state is None
            or state.position is None
            or state.source_id != grant.source_id
            or state.position.origin_id != grant.origin_id
        ):
            return self._invalidate(ControlReason.SESSION_CHANGED, now)
        return ()

    def update(
        self, state: VehicleState | None, evidence: AuthorityEvidence | None
    ) -> tuple[RequestDecision, ...]:
        """Refresh evidence, revoke on unsafe transitions and expire pending work.

        Returns cancellation outcomes. Older evidence fails closed rather than
        overriding operator/failsafe observations. Calls must be serialized.
        """
        now = self._now()
        if self._closed:
            raise RuntimeError("controller is closed")
        previous_state, previous_evidence = self._state, self._evidence
        regressed = (
            evidence is not None
            and previous_evidence is not None
            and evidence.observed_at < previous_evidence.observed_at
        )
        if (
            state is not None
            and previous_state is not None
            and state.source_id == previous_state.source_id
            and state.position is not None
            and previous_state.position is not None
            and state.position.origin_id == previous_state.position.origin_id
        ):
            regressed = regressed or (
                state.measured_at < previous_state.measured_at
                or state.received_at < previous_state.received_at
            )
        if regressed:
            return self._invalidate(ControlReason.REGRESSING_EVIDENCE, now)
        # Check the old evidence first: fresh input must not bridge an unobserved
        # stale interval and silently restore an existing lease.
        results = list(self._check(now))
        self._state, self._evidence = state, evidence
        results.extend(self._check(now))
        # Latch unsafe observations even when no lease is currently held.
        if self._policy.readiness(state, evidence, now) is not SafetyReason.ALLOWED:
            self._revoked_at = max(self._revoked_at, now)
        for key, request in tuple(self._pending.items()):
            reason = self._policy.evaluate(request, state, evidence, now)
            if reason is not SafetyReason.ALLOWED:
                del self._pending[key]
                results.append(
                    RequestDecision(key, ControlReason.SAFETY_DENIED, reason)
                )
        self._decisions.update((result.request_id, result) for result in results)
        return tuple(results)

    def decision(self, request_id: str) -> RequestDecision | None:
        """Last recorded outcome, not a permission token or a freshness check."""
        return self._decisions.get(request_id)

    def grant(self, owner_id: str, lifetime_s: float) -> AuthorityGrant:
        """Issue explicitly; never replace or renew an active lease implicitly."""
        now = self._now()
        self._check(now)
        if self._closed:
            raise RuntimeError("controller is closed")
        if (
            not owner_id.strip()
            or not isfinite(lifetime_s)
            or not (0 < lifetime_s <= self._config.max_grant_lifetime_s)
        ):
            raise ValueError("owner and bounded positive lifetime are required")
        if self._grant is not None:
            raise RuntimeError("revoke the active grant before transferring authority")
        reason = self._policy.readiness(self._state, self._evidence, now)
        if reason is not SafetyReason.ALLOWED:
            raise RuntimeError(f"authority unavailable: {reason.value}")
        state, evidence = self._state, self._evidence
        if (
            state is None
            or state.position is None
            or evidence is None
            or evidence.observed_at <= self._revoked_at
        ):
            raise RuntimeError("fresh authority evidence required after revocation")
        expires = now + lifetime_s
        if not isfinite(expires) or expires <= now:
            raise ValueError("grant deadline is not representable")
        grant = AuthorityGrant(
            uuid4().hex,
            owner_id,
            state.source_id,
            state.position.origin_id,
            now,
            expires,
        )
        self._grant = grant
        return grant

    def revoke(self) -> tuple[RequestDecision, ...]:
        return self._invalidate(ControlReason.REVOKED, self._now())

    def queue(self, request: PositionRequest) -> RequestDecision:
        now = self._now()
        self._check(now)
        key = request.request_id
        if self._closed:
            return RequestDecision(key, ControlReason.CLOSED)
        if key in self._decisions:
            return RequestDecision(key, ControlReason.DUPLICATE)
        if len(self._decisions) >= self._config.request_id_budget:
            return RequestDecision(key, ControlReason.ID_BUDGET)
        reason = self._admit(request, now)
        if reason is None:
            if len(self._pending) >= self._config.pending_limit:
                reason = RequestDecision(key, ControlReason.CAPACITY)
            else:
                self._pending[key] = request
                reason = RequestDecision(key, ControlReason.QUEUED)
        self._decisions[key] = reason
        return reason

    def _admit(self, request: PositionRequest, now: float) -> RequestDecision | None:
        grant = self._grant
        if (
            grant is None
            or request.authority_id != grant.authority_id
            or request.issued_at < grant.issued_at
        ):
            return RequestDecision(request.request_id, ControlReason.NO_GRANT)
        reason = self._policy.evaluate(request, self._state, self._evidence, now)
        if reason is not SafetyReason.ALLOWED:
            return RequestDecision(
                request.request_id, ControlReason.SAFETY_DENIED, reason
            )
        return None

    def release(self, request_id: str) -> RequestDecision:
        """Consume once after rechecking; does not call any vehicle adapter."""
        now = self._now()
        cancellations = self._check(now)
        for result in cancellations:
            if result.request_id == request_id:
                return result
        request = self._pending.pop(request_id, None)
        if request is None:
            return RequestDecision(request_id, ControlReason.UNKNOWN_REQUEST)
        denied = self._admit(request, now)
        result = denied or RequestDecision(request_id, ControlReason.AUTHORIZED)
        self._decisions[request_id] = result
        if result.reason is ControlReason.AUTHORIZED:
            self._released[request_id] = request
        return result

    def revalidate(self, request: PositionRequest) -> RequestDecision:
        """Recheck an exact released request for execution, never dispatch it."""
        now = self._now()
        self._check(now)
        if self._closed:
            return RequestDecision(request.request_id, ControlReason.CLOSED)
        if self._released.get(request.request_id) != request:
            return RequestDecision(request.request_id, ControlReason.NO_GRANT)
        return self._admit(request, now) or RequestDecision(
            request.request_id, ControlReason.AUTHORIZED
        )

    def cancel(self, request_id: str) -> RequestDecision:
        """Cancel pending work; an already released request cannot be recalled."""
        self._now()
        if self._pending.pop(request_id, None) is None:
            return RequestDecision(request_id, ControlReason.UNKNOWN_REQUEST)
        result = RequestDecision(request_id, ControlReason.CANCELLED)
        self._decisions[request_id] = result
        return result

    def close(self) -> tuple[RequestDecision, ...]:
        self._closed = True
        return self._invalidate(ControlReason.CLOSED, self._now())
