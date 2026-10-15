"""Stage 3 local policy scenarios; no flight or network operations."""

from dataclasses import dataclass, replace

import pytest

from arwen.config.models import TimingConfig
from arwen.config.safety import SafetyConfig
from arwen.control.authority import (
    AuthorityController,
    AuthorityGrant,
    ControlReason,
)
from arwen.safety.policy import PositionSafetyPolicy, SafetyReason
from arwen.vehicle.authority import AuthorityEvidence, AuthorityMode
from arwen.vehicle.contracts import LocalPosition, PositionRequest, VehicleState


@dataclass
class Clock:
    now: float = 100.0

    def __call__(self) -> float:
        return self.now


def config(pending: int = 4, budget: int = 8) -> SafetyConfig:
    return SafetyConfig(TimingConfig(1, 2), 1, 5, pending, budget)


def state(
    now: float = 100, origin: str = "session1", source: str = "vehicle1"
) -> VehicleState:
    return VehicleState(source, now, now, LocalPosition(0, 0, 0, origin))


def evidence(
    now: float = 100,
    mode: AuthorityMode = AuthorityMode.AUTONOMY_PERMITTED,
    healthy: bool = True,
    source: str = "vehicle1",
) -> AuthorityEvidence:
    return AuthorityEvidence(source, now, healthy, mode)


def request(
    grant: AuthorityGrant, key: str = "request1", now: float = 100, expires: float = 102
) -> PositionRequest:
    return PositionRequest(
        key, grant.authority_id, LocalPosition(1, 2, -3, grant.origin_id), now, expires
    )


@pytest.fixture
def rig() -> tuple[Clock, AuthorityController, AuthorityGrant]:
    clock = Clock()
    controller = AuthorityController(config(), clock)
    controller.update(state(), evidence())
    grant = controller.grant("mission1", 5)
    return clock, controller, grant


def test_explicit_ownership_and_one_shot(
    rig: tuple[Clock, AuthorityController, AuthorityGrant],
) -> None:
    _, controller, grant = rig
    assert grant.owner_id == "mission1"
    assert grant.source_id == "vehicle1" and grant.origin_id == "session1"
    assert controller.queue(request(grant)).reason is ControlReason.QUEUED
    with pytest.raises(RuntimeError, match="revoke"):
        controller.grant("mission2", 1)
    assert controller.release("request1").reason is ControlReason.AUTHORIZED
    assert controller.release("request1").reason is ControlReason.UNKNOWN_REQUEST
    assert controller.queue(request(grant)).reason is ControlReason.DUPLICATE


def test_unknown_defaults() -> None:
    controller = AuthorityController(config(), Clock())
    with pytest.raises(RuntimeError, match="missing_evidence"):
        controller.grant("mission1", 1)
    controller.update(state(), evidence(mode=AuthorityMode.UNKNOWN))
    with pytest.raises(RuntimeError, match="unknown_authority"):
        controller.grant("mission1", 1)


@pytest.mark.parametrize(
    "mode,healthy,expected",
    [
        (AuthorityMode.OPERATOR, True, SafetyReason.OPERATOR_TAKEOVER),
        (AuthorityMode.FAILSAFE, True, SafetyReason.FAILSAFE),
        (AuthorityMode.UNKNOWN, True, SafetyReason.UNKNOWN_AUTHORITY),
        (AuthorityMode.AUTONOMY_PERMITTED, False, SafetyReason.DISCONNECTED),
    ],
)
def test_takeover_loss_and_explicit_reacquisition(
    rig: tuple[Clock, AuthorityController, AuthorityGrant],
    mode: AuthorityMode,
    healthy: bool,
    expected: SafetyReason,
) -> None:
    clock, controller, grant = rig
    controller.queue(request(grant))
    clock.now = 100.1
    cancelled = controller.update(state(clock.now), evidence(clock.now, mode, healthy))
    assert len(cancelled) == 1 and cancelled[0].safety_reason is expected
    assert controller.release("request1").reason is ControlReason.UNKNOWN_REQUEST
    with pytest.raises(RuntimeError):
        controller.grant("mission2", 1)
    # Cached pre-takeover evidence cannot restore permission.
    assert controller.update(state(), evidence()) == ()
    with pytest.raises(RuntimeError):
        controller.grant("mission2", 1)
    clock.now = 100.2
    controller.update(state(clock.now), evidence(clock.now))
    replacement = controller.grant("mission2", 1)
    assert replacement.authority_id != grant.authority_id
    old = request(grant, "old", clock.now, 101)
    assert controller.queue(old).reason is ControlReason.NO_GRANT
    assert (
        controller.queue(request(replacement, "new", clock.now, 101)).reason
        is ControlReason.QUEUED
    )


def test_revocation_and_cancellation(
    rig: tuple[Clock, AuthorityController, AuthorityGrant],
) -> None:
    clock, controller, grant = rig
    controller.queue(request(grant))
    controller.queue(request(grant, "second"))
    assert controller.cancel("request1").reason is ControlReason.CANCELLED
    assert controller.cancel("request1").reason is ControlReason.UNKNOWN_REQUEST
    assert controller.queue(request(grant)).reason is ControlReason.DUPLICATE
    cancelled = controller.revoke()
    assert [r.request_id for r in cancelled] == ["second"]
    assert cancelled[0].reason is ControlReason.REVOKED
    assert controller.revoke() == ()
    with pytest.raises(RuntimeError, match="fresh"):
        controller.grant("mission2", 1)
    clock.now += 0.1
    controller.update(state(clock.now), evidence(clock.now))
    assert controller.grant("mission2", 1).owner_id == "mission2"


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({"issued_at": 100.1}, SafetyReason.FUTURE_REQUEST),
        ({"issued_at": 100, "expires_at": 102.1}, SafetyReason.EXCESSIVE_LIFETIME),
        ({"target": LocalPosition(0, 0, 0, "different")}, SafetyReason.FRAME_MISMATCH),
    ],
)
def test_request_rejections(
    rig: tuple[Clock, AuthorityController, AuthorityGrant],
    changes: dict[str, object],
    expected: SafetyReason,
) -> None:
    _, controller, grant = rig
    outcome = controller.queue(replace(request(grant), **changes))
    assert outcome.reason is ControlReason.SAFETY_DENIED
    assert outcome.safety_reason is expected


def test_forged_and_pregrant_requests(
    rig: tuple[Clock, AuthorityController, AuthorityGrant],
) -> None:
    _, controller, grant = rig
    assert (
        controller.queue(replace(request(grant), authority_id="made-up")).reason
        is ControlReason.NO_GRANT
    )
    assert (
        controller.queue(replace(request(grant, "old"), issued_at=99)).reason
        is ControlReason.NO_GRANT
    )


def test_expiry_rechecked_at_release(
    rig: tuple[Clock, AuthorityController, AuthorityGrant],
) -> None:
    clock, controller, grant = rig
    controller.queue(request(grant, expires=100.5))
    clock.now = 100.5
    result = controller.release("request1")
    assert result.reason is ControlReason.SAFETY_DENIED
    assert result.safety_reason is SafetyReason.EXPIRED_REQUEST


def test_expiry_update_reports_id(
    rig: tuple[Clock, AuthorityController, AuthorityGrant],
) -> None:
    clock, controller, grant = rig
    controller.queue(request(grant, expires=100.5))
    clock.now = 100.5
    results = controller.update(state(clock.now), evidence(clock.now))
    assert len(results) == 1
    assert results[0].safety_reason is SafetyReason.EXPIRED_REQUEST


@pytest.mark.parametrize("which", ["state", "authority", "both"])
def test_exact_freshness_limit_revokes_before_recovery(
    rig: tuple[Clock, AuthorityController, AuthorityGrant],
    which: str,
) -> None:
    clock, controller, grant = rig
    controller.queue(request(grant))
    clock.now = 101
    # Fresh replacement input cannot bridge a stale interval for the old lease.
    results = controller.update(
        state(clock.now) if which != "state" else state(),
        evidence(clock.now) if which != "authority" else evidence(),
    )
    assert len(results) == 1 and results[0].reason is ControlReason.SAFETY_DENIED
    assert (
        controller.queue(request(grant, "later", 101, 102)).reason
        is ControlReason.NO_GRANT
    )


def test_grant_exclusive_expiry() -> None:
    clock = Clock()
    controller = AuthorityController(config(), clock)
    controller.update(state(), evidence())
    grant = controller.grant("mission", 0.5)
    controller.queue(request(grant))
    clock.now = 100.5
    result = controller.release("request1")
    assert result.reason is ControlReason.GRANT_EXPIRED
    with pytest.raises(RuntimeError, match="fresh"):
        controller.grant("mission", 1)


@pytest.mark.parametrize("source,origin", [("other", "session1"), ("vehicle1", "new")])
def test_source_and_session_changes(
    rig: tuple[Clock, AuthorityController, AuthorityGrant],
    source: str,
    origin: str,
) -> None:
    clock, controller, grant = rig
    controller.queue(request(grant))
    clock.now = 100.1
    results = controller.update(
        state(clock.now, origin, source), evidence(clock.now, source=source)
    )
    assert results[0].reason is ControlReason.SESSION_CHANGED
    assert controller.release("request1").reason is ControlReason.UNKNOWN_REQUEST


@pytest.mark.parametrize("missing", ["state", "position", "evidence"])
def test_missing_inputs_revoke(
    rig: tuple[Clock, AuthorityController, AuthorityGrant],
    missing: str,
) -> None:
    _, controller, grant = rig
    controller.queue(request(grant))
    new_state = None if missing == "state" else state()
    if missing == "position":
        new_state = replace(state(), position=None)
    results = controller.update(
        new_state, None if missing == "evidence" else evidence()
    )
    assert len(results) == 1 and results[0].reason is ControlReason.SAFETY_DENIED


def test_replay_and_capacity_bound() -> None:
    controller = AuthorityController(config(1, 2), Clock())
    controller.update(state(), evidence())
    grant = controller.grant("mission", 2)
    assert controller.queue(request(grant)).reason is ControlReason.QUEUED
    assert controller.queue(request(grant, "second")).reason is ControlReason.CAPACITY
    controller.cancel("request1")
    assert controller.queue(request(grant, "second")).reason is ControlReason.DUPLICATE
    assert controller.queue(request(grant, "third")).reason is ControlReason.ID_BUDGET
    assert controller.queue(request(grant)).reason is ControlReason.DUPLICATE


@pytest.mark.parametrize("bad_time", [99, -1, float("nan"), float("inf")])
def test_clock_error_revokes(
    rig: tuple[Clock, AuthorityController, AuthorityGrant],
    bad_time: float,
) -> None:
    clock, controller, grant = rig
    controller.queue(request(grant))
    clock.now = bad_time
    with pytest.raises(ValueError):
        controller.release("request1")
    clock.now = 100
    assert controller.release("request1").reason is ControlReason.UNKNOWN_REQUEST
    assert controller.queue(request(grant, "second")).reason is ControlReason.NO_GRANT


def test_close_is_terminal(
    rig: tuple[Clock, AuthorityController, AuthorityGrant],
) -> None:
    _, controller, grant = rig
    controller.queue(request(grant))
    assert controller.close()[0].reason is ControlReason.CLOSED
    assert controller.close() == ()
    assert controller.queue(request(grant, "second")).reason is ControlReason.CLOSED
    with pytest.raises(RuntimeError):
        controller.grant("mission", 1)
    with pytest.raises(RuntimeError):
        controller.update(state(), evidence())


@pytest.mark.parametrize("attribute", ["measured_at", "received_at"])
def test_regressing_position_revokes(
    rig: tuple[Clock, AuthorityController, AuthorityGrant],
    attribute: str,
) -> None:
    _, controller, grant = rig
    controller.queue(request(grant))
    older = replace(
        state(),
        measured_at=99.9,
        **({"received_at": 99.9} if attribute == "received_at" else {}),
    )
    assert (
        controller.update(older, evidence())[0].reason
        is ControlReason.REGRESSING_EVIDENCE
    )


@pytest.mark.parametrize(
    "owner,duration", [("", 1), (" ", 1), ("x", 0), ("x", 6), ("x", float("nan"))]
)
def test_invalid_grants(owner: str, duration: float) -> None:
    controller = AuthorityController(config(), Clock())
    controller.update(state(), evidence())
    with pytest.raises(ValueError):
        controller.grant(owner, duration)


@pytest.mark.parametrize(
    "changes",
    [
        {"pending_limit": 0},
        {"request_id_budget": 0},
        {"pending_limit": 10, "request_id_budget": 2},
        {"max_authority_age_s": float("nan")},
        {"max_grant_lifetime_s": 0},
    ],
)
def test_invalid_config(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        replace(config(), **changes)


def test_policy_denies_unknown_future_and_mismatched_evidence() -> None:
    policy = PositionSafetyPolicy(config())
    assert policy.readiness(state(), evidence(101), 100) is SafetyReason.STALE_AUTHORITY
    assert (
        policy.readiness(state(), evidence(source="other"), 100)
        is SafetyReason.SOURCE_MISMATCH
    )
    with pytest.raises(ValueError):
        policy.readiness(state(), evidence(), float("nan"))
    for timestamp in (-1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            evidence(timestamp)


def test_cancellation_diagnostics_retained(
    rig: tuple[Clock, AuthorityController, AuthorityGrant],
) -> None:
    clock, controller, grant = rig
    controller.queue(request(grant))
    clock.now = 101
    # A queue operation detects stale evidence and cancels all old pending IDs.
    controller.queue(request(grant, "later", now=101))
    recorded = controller.decision("request1")
    assert recorded is not None and recorded.reason is ControlReason.SAFETY_DENIED
    assert recorded.safety_reason is SafetyReason.STALE_AUTHORITY
    assert controller.decision("never-seen") is None


def test_synthetic_application(capsys: pytest.CaptureFixture[str]) -> None:
    from arwen.applications.authority_demo import main

    assert main() == 0
    output = capsys.readouterr().out
    assert "operator_takeover" in output
    assert "pending request cancelled" in output
    assert "unknown_request" in output
    assert "no vehicle commands were sent" in output
