"""Serialized dispatch and outcome tracking; no firmware-specific command code."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from math import dist, isfinite

from arwen.config.execution import ExecutionConfig
from arwen.control.authority import AuthorityController, ControlReason, RequestDecision
from arwen.vehicle.authority import AuthorityEvidence
from arwen.vehicle.contracts import PositionRequest, RequestStatus, VehicleState
from arwen.vehicle.execution import AckStatus, ExecutionAdapter, StopStatus


class ExecutionPhase(Enum):
    SUBMITTED = "submitted"
    ACKNOWLEDGED = "acknowledged"
    COMPLETED = "completed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    ACK_TIMEOUT = "ack_timeout"
    COMPLETION_TIMEOUT = "completion_timeout"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    request_id: str
    phase: ExecutionPhase
    detail: str


class ExecutionController:
    """One request at a time, one application owner, no reentrant adapter calls.

    Call update regularly and close before closing the adapter. A confirmed stop
    prevents future local output only; already submitted effects may continue.
    """

    def __init__(
        self,
        authority: AuthorityController,
        adapter: ExecutionAdapter,
        config: ExecutionConfig,
        clock: Callable[[], float],
    ) -> None:
        self._authority = authority
        self._adapter = adapter
        self._config = config
        self._clock = clock
        self._last_now = 0.0
        self._active: PositionRequest | None = None
        self._source_id = ""
        self._sent_at = 0.0
        self._acknowledged = False
        self._history: dict[str, ExecutionOutcome] = {}
        self._closed = False
        self._fault: str | None = None

    @property
    def fault(self) -> str | None:
        return self._fault

    def outcome(self, request_id: str) -> ExecutionOutcome | None:
        """Last recorded outcome, not a live health check."""
        return self._history.get(request_id)

    def _now(self) -> float:
        now = self._clock()
        if not isfinite(now) or now < self._last_now:
            raise ValueError(
                "execution clock must be finite, nonnegative and monotonic"
            )
        self._last_now = now
        return now

    def _finish(self, phase: ExecutionPhase, detail: str) -> ExecutionOutcome:
        request = self._active
        if request is None:
            raise RuntimeError("no active request")
        # Clear first: a stop exception cannot leave work eligible for retry.
        self._active = None
        try:
            if self._adapter.stop(request.request_id) is not StopStatus.STOPPED:
                self._fault = "adapter did not confirm stopping future local output"
        except Exception as exc:
            self._fault = f"adapter stop failed: {exc}"
        if self._fault is not None:
            try:
                self._authority.revoke()
            except Exception as revoke_error:
                self._fault += f"; authority revocation failed: {revoke_error}"
            phase = ExecutionPhase.FAILED
            detail = f"{detail}; {self._fault}"
        result = ExecutionOutcome(request.request_id, phase, detail)
        self._history[request.request_id] = result
        return result

    def _error(self, exc: Exception) -> None:
        self._fault = f"execution fault: {type(exc).__name__}: {exc}"
        try:
            self._authority.revoke()
        except Exception as revoke_error:
            self._fault += f"; authority revocation failed: {revoke_error}"
        finally:
            if self._active is not None:
                self._finish(ExecutionPhase.FAILED, self._fault)

    @staticmethod
    def _denial(decision: RequestDecision) -> str:
        return (
            decision.safety_reason.value
            if decision.safety_reason
            else decision.reason.value
        )

    def start(
        self,
        request: PositionRequest,
        evidence: AuthorityEvidence | None,
        *,
        admit_before: float | None = None,
    ) -> ExecutionOutcome:
        key = request.request_id

        def rejected(detail: str) -> ExecutionOutcome:
            return ExecutionOutcome(key, ExecutionPhase.REJECTED, detail)

        if admit_before is not None and (
            not isfinite(admit_before) or admit_before < 0
        ):
            return rejected("admission deadline must be finite and nonnegative")
        if self._closed or self._fault is not None:
            return rejected(self._fault or "execution controller closed")
        if key in self._history:
            return rejected("duplicate request ID")
        if self._active is not None:
            return rejected("another request is active")
        if len(self._history) >= self._config.history_limit:
            return rejected("execution history budget exhausted")
        try:
            self._now()
            state = self._adapter.snapshot()
            self._authority.update(state, evidence)
            queued = self._authority.queue(request)
            if queued.reason is not ControlReason.QUEUED:
                result = rejected(self._denial(queued))
                self._history[key] = result
                return result
            if not self._adapter.capabilities.local_position_requests:
                self._authority.cancel(key)
                result = rejected("adapter does not support position requests")
                self._history[key] = result
                return result
            released = self._authority.release(key)
            if released.reason is not ControlReason.AUTHORIZED:
                result = rejected(self._denial(released))
                self._history[key] = result
                return result
            # No await, queue, callback or application work between final check
            # and submit. The adapter must also enforce its own native limits.
            sent_at = self._now()
            checked = self._authority.revalidate(request)
            if checked.reason is not ControlReason.AUTHORIZED:
                result = rejected(self._denial(checked))
                self._history[key] = result
                return result
            if admit_before is not None and self._now() >= admit_before:
                self._authority.cancel(key)
                result = rejected("intent admission deadline expired")
                self._history[key] = result
                return result
            if state is None:
                raise RuntimeError("authorized without a vehicle state")
            self._source_id = state.source_id
            self._sent_at = sent_at
            self._acknowledged = False
            self._active = request
            submission = self._adapter.submit(request)
            if submission.request_id != key:
                raise RuntimeError("adapter submission correlation mismatch")
            if submission.status is not RequestStatus.ACCEPTED:
                return self._finish(ExecutionPhase.REJECTED, submission.status.value)
            guarded = self._guard(request)
            if guarded is not None:
                return guarded
            outcome = ExecutionOutcome(
                key,
                ExecutionPhase.SUBMITTED,
                "local submission accepted; awaiting acknowledgement",
            )
            self._history[key] = outcome
            return outcome
        except Exception as exc:
            self._error(exc)
            outcome = self._history.get(key) or ExecutionOutcome(
                key, ExecutionPhase.FAILED, self._fault or "execution fault"
            )
            self._history[key] = outcome
            return outcome

    def update(self, evidence: AuthorityEvidence | None) -> ExecutionOutcome | None:
        if self._closed or self._fault is not None:
            return None
        try:
            now = self._now()
            state = self._adapter.snapshot()
            self._authority.update(state, evidence)
            request = self._active
            if request is None:
                return None
            guarded = self._guard(request)
            if guarded is not None:
                return guarded
            for _ in range(self._config.feedback_budget):
                ack = self._adapter.acknowledgement()
                guarded = self._guard(request)
                if guarded is not None:
                    return guarded
                now = self._now()
                if ack is None:
                    break
                if (
                    ack.request_id != request.request_id
                    or ack.source_id != self._source_id
                    or ack.origin_id != request.target.origin_id
                    or not self._sent_at <= ack.observed_at <= now
                ):
                    continue
                if ack.status is AckStatus.REJECTED:
                    return self._finish(
                        ExecutionPhase.REJECTED, "controller rejected request"
                    )
                self._acknowledged = True
            if self._acknowledged:
                if self._arrived(request, state):
                    return self._finish(
                        ExecutionPhase.COMPLETED,
                        "fresh post-submission position within tolerance",
                    )
                result = ExecutionOutcome(
                    request.request_id,
                    ExecutionPhase.ACKNOWLEDGED,
                    "controller accepted; physical completion unobserved",
                )
                self._history[request.request_id] = result
                return result
            return self._history[request.request_id]
        except Exception as exc:
            key = self._active.request_id if self._active else None
            self._error(exc)
            return self._history.get(key) if key else None

    def _guard(self, request: PositionRequest) -> ExecutionOutcome | None:
        checked = self._authority.revalidate(request)
        if checked.reason is not ControlReason.AUTHORIZED:
            return self._finish(ExecutionPhase.INTERRUPTED, self._denial(checked))
        now = self._now()
        if now - self._sent_at >= self._config.completion_timeout_s:
            return self._finish(
                ExecutionPhase.COMPLETION_TIMEOUT, "observed completion deadline"
            )
        if (
            not self._acknowledged
            and now - self._sent_at >= self._config.acknowledgement_timeout_s
        ):
            return self._finish(ExecutionPhase.ACK_TIMEOUT, "acknowledgement deadline")
        return None

    def _arrived(self, request: PositionRequest, state: VehicleState | None) -> bool:
        if (
            state is None
            or state.position is None
            or state.measured_at <= self._sent_at
        ):
            return False
        position, target = state.position, request.target
        return (
            dist(
                (position.north_m, position.east_m, position.down_m),
                (target.north_m, target.east_m, target.down_m),
            )
            <= self._config.position_tolerance_m
        )

    def cancel(self) -> ExecutionOutcome | None:
        if self._active is None:
            return None
        return self._finish(
            ExecutionPhase.CANCELLED,
            "tracking cancelled; prior physical effects are not recalled",
        )

    def close(self) -> ExecutionOutcome | None:
        self._closed = True
        try:
            return self.cancel()
        finally:
            try:
                self._authority.close()
            except Exception as exc:
                self._fault = f"authority shutdown failed: {exc}"
