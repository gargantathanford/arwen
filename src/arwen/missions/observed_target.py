"""One-shot mission policy. Emits intent, never accesses adapters or control."""

from dataclasses import dataclass
from enum import Enum
from math import dist, isfinite
from uuid import uuid4

from arwen.config.missions import ObservedTargetConfig
from arwen.perception.observations import TargetObservation
from arwen.vehicle.contracts import LocalPosition, VehicleState


class MissionPhase(Enum):
    WAITING = "waiting"
    READY = "ready"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    ABORTED = "aborted"


class MissionReason(Enum):
    WAITING_STATE = "waiting_state"
    WAITING_OBSERVATION = "waiting_observation"
    STALE_STATE = "stale_state"
    STALE_OBSERVATION = "stale_observation"
    WRONG_SOURCE = "wrong_source"
    WRONG_OBSERVATION = "wrong_observation"
    UNKNOWN_POSITION = "unknown_position"
    FRAME_MISMATCH = "frame_mismatch"
    LOW_CONFIDENCE = "low_confidence"
    UNCERTAIN_POSITION = "uncertain_position"
    REGRESSING_DATA = "regressing_data"
    CONFLICTING_SAMPLE = "conflicting_sample"
    TARGET_MOVED = "target_moved"
    INTENT_EXPIRED = "intent_expired"
    READY = "ready"
    EXECUTING = "executing"
    EXECUTION_COMPLETED = "execution_completed"
    EXECUTION_FAILED = "execution_failed"
    CANCELLED = "cancelled"
    CLOCK_ERROR = "clock_error"
    APPLICATION_ERROR = "application_error"


@dataclass(frozen=True, slots=True)
class PositionIntent:
    """Desired position with an admission deadline, not a command or authority."""

    intent_id: str
    target: LocalPosition
    created_at: float
    admit_before: float

    def __post_init__(self) -> None:
        if not self.intent_id.strip():
            raise ValueError("intent identity must be nonempty")
        if (
            not all(
                isfinite(t) and t >= 0 for t in (self.created_at, self.admit_before)
            )
            or self.admit_before <= self.created_at
        ):
            raise ValueError("intent requires a finite exclusive admission deadline")


@dataclass(frozen=True, slots=True)
class MissionSnapshot:
    phase: MissionPhase
    reason: MissionReason
    intent: PositionIntent | None


class ObservedTargetMission:
    def __init__(self, config: ObservedTargetConfig) -> None:
        self._config = config
        self._snapshot = MissionSnapshot(
            MissionPhase.WAITING, MissionReason.WAITING_STATE, None
        )
        self._last_now = 0.0
        self._last_observation: TargetObservation | None = None
        self._last_state: VehicleState | None = None

    @property
    def snapshot(self) -> MissionSnapshot:
        return self._snapshot

    @property
    def terminal(self) -> bool:
        return self._snapshot.phase in (MissionPhase.SUCCEEDED, MissionPhase.ABORTED)

    def abort(self, reason: MissionReason = MissionReason.CANCELLED) -> MissionSnapshot:
        if not self.terminal:
            self._snapshot = MissionSnapshot(
                MissionPhase.ABORTED, reason, self._snapshot.intent
            )
        return self._snapshot

    def _unavailable(self, reason: MissionReason) -> MissionSnapshot:
        if self._snapshot.phase is MissionPhase.WAITING:
            self._snapshot = MissionSnapshot(MissionPhase.WAITING, reason, None)
        else:
            self.abort(reason)
        return self._snapshot

    def evaluate(
        self,
        state: VehicleState | None,
        observation: TargetObservation | None,
        now: float,
    ) -> MissionSnapshot:
        if self.terminal:
            return self._snapshot
        if not isfinite(now) or now < self._last_now:
            self.abort(MissionReason.CLOCK_ERROR)
            raise ValueError("mission clock must be finite, nonnegative and monotonic")
        self._last_now = now
        if state is None or state.position is None:
            return self._unavailable(MissionReason.WAITING_STATE)
        if state.source_id != self._config.vehicle_source_id:
            return self._unavailable(MissionReason.WRONG_SOURCE)
        if not state.position_is_fresh(now, self._config.max_state_age_s):
            return self._unavailable(MissionReason.STALE_STATE)
        intent = self._snapshot.intent
        if intent is not None and state.position.origin_id != intent.target.origin_id:
            return self.abort(MissionReason.FRAME_MISMATCH)
        if self._snapshot.phase is MissionPhase.EXECUTING:
            # Fresh replacement samples cannot hide a gap in ongoing evidence.
            if (
                self._last_observation is not None
                and now - self._last_observation.measured_at
                >= self._config.max_observation_age_s
            ):
                return self.abort(MissionReason.STALE_OBSERVATION)
            if (
                self._last_state is not None
                and now - self._last_state.measured_at >= self._config.max_state_age_s
            ):
                return self.abort(MissionReason.STALE_STATE)
        if observation is None:
            return self._unavailable(MissionReason.WAITING_OBSERVATION)
        if (observation.sensor_id, observation.target_id) != (
            self._config.sensor_id,
            self._config.target_id,
        ):
            return self._unavailable(MissionReason.WRONG_OBSERVATION)
        if observation.position is None:
            return self._unavailable(MissionReason.UNKNOWN_POSITION)
        if (
            observation.received_at > now
            or not 0
            <= now - observation.measured_at
            < self._config.max_observation_age_s
        ):
            return self._unavailable(MissionReason.STALE_OBSERVATION)
        if observation.position.origin_id != state.position.origin_id:
            return self._unavailable(MissionReason.FRAME_MISMATCH)
        if observation.confidence < self._config.min_confidence:
            return self._unavailable(MissionReason.LOW_CONFIDENCE)
        if observation.uncertainty_m > self._config.max_uncertainty_m:
            return self._unavailable(MissionReason.UNCERTAIN_POSITION)
        previous, previous_state = self._last_observation, self._last_state
        if previous is not None:
            if (
                observation.measured_at < previous.measured_at
                or observation.received_at < previous.received_at
            ):
                return self.abort(MissionReason.REGRESSING_DATA)
            if observation.measured_at == previous.measured_at and (
                observation.position != previous.position
                or observation.confidence != previous.confidence
                or observation.uncertainty_m != previous.uncertainty_m
            ):
                return self.abort(MissionReason.CONFLICTING_SAMPLE)
        if previous_state is not None and (
            state.measured_at < previous_state.measured_at
            or state.received_at < previous_state.received_at
        ):
            return self.abort(MissionReason.REGRESSING_DATA)
        if intent is not None:
            if (
                dist(
                    (
                        observation.position.north_m,
                        observation.position.east_m,
                        observation.position.down_m,
                    ),
                    (intent.target.north_m, intent.target.east_m, intent.target.down_m),
                )
                > self._config.max_target_shift_m
            ):
                return self.abort(MissionReason.TARGET_MOVED)
            if (
                self._snapshot.phase is MissionPhase.READY
                and now >= intent.admit_before
            ):
                return self.abort(MissionReason.INTENT_EXPIRED)
        else:
            deadline = min(
                now + self._config.intent_lifetime_s,
                state.measured_at + self._config.max_state_age_s,
                observation.measured_at + self._config.max_observation_age_s,
            )
            if not isfinite(deadline) or deadline <= now:
                return self._unavailable(MissionReason.INTENT_EXPIRED)
            intent = PositionIntent(
                f"{self._config.mission_id}:{uuid4().hex}",
                observation.position,
                now,
                deadline,
            )
            self._snapshot = MissionSnapshot(
                MissionPhase.READY, MissionReason.READY, intent
            )
        self._last_observation, self._last_state = observation, state
        return self._snapshot

    def dispatched(self, intent_id: str) -> MissionSnapshot:
        if (
            self._snapshot.phase is not MissionPhase.READY
            or self._snapshot.intent is None
            or self._snapshot.intent.intent_id != intent_id
        ):
            raise RuntimeError("only the current ready intent may be dispatched")
        self._snapshot = MissionSnapshot(
            MissionPhase.EXECUTING, MissionReason.EXECUTING, self._snapshot.intent
        )
        return self._snapshot

    def finished(self, intent_id: str, succeeded: bool) -> MissionSnapshot:
        if (
            self._snapshot.phase is not MissionPhase.EXECUTING
            or self._snapshot.intent is None
            or self._snapshot.intent.intent_id != intent_id
            or type(succeeded) is not bool
        ):
            raise RuntimeError("completion must match the executing intent")
        self._snapshot = MissionSnapshot(
            MissionPhase.SUCCEEDED if succeeded else MissionPhase.ABORTED,
            MissionReason.EXECUTION_COMPLETED
            if succeeded
            else MissionReason.EXECUTION_FAILED,
            self._snapshot.intent,
        )
        return self._snapshot
