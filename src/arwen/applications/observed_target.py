"""One-owner mission composition; no automatic grant acquisition or renewal."""

from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite

from arwen.config.execution import ExecutionConfig
from arwen.control.authority import AuthorityController, AuthorityGrant
from arwen.control.execution import (
    ExecutionController,
    ExecutionOutcome,
    ExecutionPhase,
)
from arwen.missions.observed_target import (
    MissionPhase,
    MissionReason,
    MissionSnapshot,
    ObservedTargetMission,
)
from arwen.perception.observations import TargetObservation
from arwen.vehicle.authority import AuthorityEvidence
from arwen.vehicle.contracts import PositionRequest
from arwen.vehicle.execution import ExecutionAdapter


@dataclass(frozen=True, slots=True)
class MissionRunSnapshot:
    mission: MissionSnapshot
    execution: ExecutionOutcome | None
    detail: str


class ObservedTargetApplication:
    """Own one mission, grant and execution lifecycle. Drive tick regularly.

    The selected observation stream is supplied explicitly: None means unavailable.
    This is not a sensor multiplexer. Shutdown stops future local output, never
    claims to undo physical effects. All inputs share one host monotonic domain.
    """

    def __init__(
        self,
        mission: ObservedTargetMission,
        authority: AuthorityController,
        grant: AuthorityGrant,
        adapter: ExecutionAdapter,
        execution_config: ExecutionConfig,
        request_lifetime_s: float,
        clock: Callable[[], float],
    ) -> None:
        if not isfinite(request_lifetime_s) or request_lifetime_s <= 0:
            raise ValueError("request lifetime must be finite and positive")
        self._mission = mission
        self._grant = grant
        self._adapter = adapter
        self._clock = clock
        self._request_lifetime_s = request_lifetime_s
        self._execution = ExecutionController(
            authority, adapter, execution_config, clock
        )
        self._outcome: ExecutionOutcome | None = None
        self._detail = "waiting for a qualifying observation"
        self._closed = False

    @property
    def snapshot(self) -> MissionRunSnapshot:
        return MissionRunSnapshot(self._mission.snapshot, self._outcome, self._detail)

    def _shutdown(self) -> None:
        self._closed = True
        result = self._execution.close()
        if result is not None:
            self._outcome = result
        if self._execution.fault is not None:
            self._detail = self._execution.fault

    def tick(
        self, observation: TargetObservation | None, evidence: AuthorityEvidence | None
    ) -> MissionRunSnapshot:
        if self._closed:
            return self.snapshot
        try:
            now = self._clock()
            mission = self._mission.evaluate(self._adapter.snapshot(), observation, now)
            if self._mission.terminal:
                self._detail = mission.reason.value
                self._shutdown()
                return self.snapshot
            if mission.phase is MissionPhase.READY:
                intent = mission.intent
                if intent is None:
                    raise RuntimeError("ready mission has no intent")
                request = PositionRequest(
                    intent.intent_id,
                    self._grant.authority_id,
                    intent.target,
                    now,
                    now + self._request_lifetime_s,
                )
                self._outcome = self._execution.start(
                    request, evidence, admit_before=intent.admit_before
                )
                self._detail = self._outcome.detail
                if self._outcome.phase in (
                    ExecutionPhase.SUBMITTED,
                    ExecutionPhase.ACKNOWLEDGED,
                ):
                    self._mission.dispatched(intent.intent_id)
                else:
                    self._mission.abort(MissionReason.EXECUTION_FAILED)
                    self._shutdown()
            elif mission.phase is MissionPhase.EXECUTING:
                self._outcome = self._execution.update(evidence)
                if self._outcome is None or mission.intent is None:
                    raise RuntimeError("mission lost its active execution")
                self._detail = self._outcome.detail
                if self._outcome.phase is ExecutionPhase.COMPLETED:
                    self._mission.finished(mission.intent.intent_id, True)
                    self._shutdown()
                elif self._outcome.phase not in (
                    ExecutionPhase.SUBMITTED,
                    ExecutionPhase.ACKNOWLEDGED,
                ):
                    self._mission.finished(mission.intent.intent_id, False)
                    self._shutdown()
            else:
                self._execution.update(evidence)
                if self._execution.fault is not None:
                    raise RuntimeError(self._execution.fault)
                self._detail = mission.reason.value
        except Exception as exc:
            self._mission.abort(MissionReason.APPLICATION_ERROR)
            self._detail = f"{type(exc).__name__}: {exc}"
            self._shutdown()
        return self.snapshot

    def close(self) -> MissionRunSnapshot:
        if not self._closed:
            self._mission.abort(MissionReason.CANCELLED)
            self._detail = "mission cancelled; prior physical effects are not recalled"
            self._shutdown()
        return self.snapshot
