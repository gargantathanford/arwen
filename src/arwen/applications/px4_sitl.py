"""Real, opt-in PX4 1.16.0 SIH flight scenario. Never run on aircraft."""

import argparse
import fcntl
from ipaddress import IPv4Address
from math import isfinite
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

from arwen.communication.mavlink import FlightUdpTransport
from arwen.communication.px4 import ConnectionState
from arwen.communication.px4_sitl import Px4SitlAdapter
from arwen.config.models import TimingConfig
from arwen.config.safety import SafetyConfig
from arwen.config.sitl import SitlConfig
from arwen.config.telemetry import TelemetryConfig
from arwen.control.arrival import ArrivalTracker
from arwen.control.authority import AuthorityController, AuthorityGrant, ControlReason
from arwen.logging.events import JsonEvents
from arwen.vehicle.authority import AuthorityEvidence, AuthorityMode
from arwen.vehicle.contracts import LocalPosition, PositionRequest, VehicleState


class SitlScenario:
    """One serial owner; only this explicit scenario may initiate flight."""

    def __init__(
        self, adapter: Px4SitlAdapter, limits: SitlConfig, events: JsonEvents
    ) -> None:
        self.adapter = adapter
        self.limits = limits
        self.events = events
        self.phase = "CONNECTING"
        self.authority = AuthorityController(
            SafetyConfig(
                TimingConfig(limits.telemetry_age_s, limits.phase_timeout_s),
                limits.telemetry_age_s,
                limits.total_timeout_s,
                1,
                16,
            ),
            monotonic,
        )
        self.grant: AuthorityGrant | None = None
        self.request: PositionRequest | None = None
        self._allowed_modes: set[tuple[int, int]] = set()
        self._last_log = 0.0
        self._started = monotonic()
        self.arrival = ArrivalTracker(limits)

    def emit(self, event: str, **fields: object) -> None:
        heartbeat = self.adapter.heartbeat
        state = self.adapter.snapshot()
        position = state.position if state else None
        target = self.adapter.target
        self.events.write(
            event,
            {
                "phase": self.phase,
                "source_id": state.source_id if state else None,
                "origin_id": position.origin_id if position else None,
                "mode": list(heartbeat.mode) if heartbeat else None,
                "armed": heartbeat.armed if heartbeat else None,
                "landed_state": self.adapter.landed,
                "request_id": self.request.request_id if self.request else None,
                "position_ned_m": [position.north_m, position.east_m, position.down_m]
                if position
                else None,
                "target_ned_m": [target.north_m, target.east_m, target.down_m]
                if target
                else None,
                **fields,
            },
        )

    def transition(self, phase: str) -> None:
        previous, self.phase = self.phase, phase
        self.emit("transition", previous=previous, state=phase)
        print(phase, flush=True)

    def _state(self) -> VehicleState:
        state = self.adapter.snapshot()
        if state is None or state.position is None:
            raise RuntimeError("aligned position unavailable")
        return state

    def pump(self) -> None:
        self.adapter.poll()
        now = monotonic()
        if now - self._started >= self.limits.total_timeout_s:
            raise RuntimeError("total scenario deadline")
        if self.grant is not None:
            reason = self.adapter.readiness()
            heartbeat = self.adapter.heartbeat
            if reason is not None or heartbeat is None:
                raise RuntimeError(reason or "heartbeat unavailable")
            mode_ok = heartbeat.mode in self._allowed_modes or (
                self.phase == "WAIT_LANDED_DISARMED"
                and not heartbeat.armed
                and self.adapter.landed == 1
                and heartbeat.mode in {(6, 0), (4, 3)}
            )
            state = self._state()
            evidence = AuthorityEvidence(
                state.source_id,
                self.adapter.heartbeat_at,
                True,
                AuthorityMode.AUTONOMY_PERMITTED if mode_ok else AuthorityMode.OPERATOR,
            )
            self.authority.update(state, evidence)
            current = self.authority.current_grant()
            if current is None or current.authority_id != self.grant.authority_id:
                raise RuntimeError("operator takeover or revoked/expired authority")
            if not mode_ok:
                raise RuntimeError(
                    f"operator takeover: unexpected mode {heartbeat.mode}"
                )
            if self.phase in ("TAKEOFF", "MOVING") and not heartbeat.armed:
                raise RuntimeError("unexpected disarm during motion")
            if self.request is not None:
                decision = self.authority.revalidate(self.request)
                if decision.reason is not ControlReason.AUTHORIZED:
                    raise RuntimeError(
                        f"position request no longer authorized: {decision}"
                    )
            self.adapter.stream()
        if now - self._last_log >= 0.2:
            self.emit("telemetry", readiness=self.adapter.readiness())
            self._last_log = now
        sleep(0.005)

    def wait_ready(self, timeout: float) -> None:
        deadline = monotonic() + timeout
        ready_since: float | None = None
        origin: str | None = None
        while monotonic() < deadline:
            self.pump()
            state = self.adapter.snapshot()
            if (
                self.adapter.readiness() is None
                and self.adapter.transaction is not None
                and self.adapter.transaction.result == 0
                and state is not None
                and state.position is not None
            ):
                if origin != state.position.origin_id:
                    ready_since = monotonic()
                    origin = state.position.origin_id
                if ready_since is not None and monotonic() - ready_since >= 2:
                    return
            else:
                ready_since = None
                origin = None
        raise RuntimeError(f"preflight deadline: {self.adapter.readiness()}")

    def wait_command(self, observed: str) -> None:
        deadline = monotonic() + self.limits.command_timeout_s
        while monotonic() < deadline:
            self.pump()
            pending, heartbeat = self.adapter.transaction, self.adapter.heartbeat
            if pending is None or pending.result != 0 or heartbeat is None:
                continue
            confirmed = (
                observed == "OFFBOARD"
                and heartbeat.mode == (6, 0)
                or observed == "ARMED"
                and heartbeat.armed
                or observed == "LAND"
                and heartbeat.mode == (4, 6)
            )
            if confirmed and self.adapter.heartbeat_at > pending.sent_at:
                self.emit(
                    "command_observed",
                    command=pending.command,
                    request_id=pending.request_id,
                    observed=observed,
                )
                return
        raise RuntimeError(f"requested {observed} not observed before deadline")

    def move(self, target: LocalPosition, phase: str) -> None:
        if self.grant is None:
            raise RuntimeError("no operator grant")
        now = monotonic()
        request = PositionRequest(
            f"{phase.lower()}-{uuid4().hex}",
            self.grant.authority_id,
            target,
            now,
            now + self.limits.phase_timeout_s,
        )
        if self.authority.queue(request).reason is not ControlReason.QUEUED:
            raise RuntimeError("position intent denied")
        if (
            self.authority.release(request.request_id).reason
            is not ControlReason.AUTHORIZED
        ):
            raise RuntimeError("position intent release denied")
        self.request = request
        self.adapter.set_target(target, request.request_id)
        self.arrival.reset()
        self.transition(phase)
        while monotonic() < request.expires_at:
            self.pump()
            if self.arrival.update(self._state(), target, monotonic()):
                self.emit("arrival_observed", dwell_s=self.limits.dwell_s)
                return
        raise RuntimeError(f"{phase} observed-arrival deadline")

    def run(
        self, *, operator_authorized: bool, mode_rejection_test: bool = False
    ) -> None:
        self.transition("PREFLIGHT")
        self.wait_ready(45)
        heartbeat, state = self.adapter.heartbeat, self._state()
        if heartbeat is None or heartbeat.armed or self.adapter.landed != 1:
            raise RuntimeError("preflight requires observed landed and disarmed")
        self.emit(
            "preflight_validated",
            parameters=self.adapter.parameters,
            firmware_sw_version=self.adapter.version.software
            if self.adapter.version
            else None,
        )
        if not operator_authorized:
            self.transition("READY_NO_FLIGHT_AUTHORIZATION")
            return
        initial = state.position
        if initial is None:
            raise RuntimeError("position unavailable")
        evidence = AuthorityEvidence(
            state.source_id,
            self.adapter.heartbeat_at,
            True,
            AuthorityMode.AUTONOMY_PERMITTED,
        )
        self.authority.update(state, evidence)
        self.grant = self.authority.grant(
            "explicit-sitl-operator", self.limits.total_timeout_s
        )
        self.adapter.authorize(
            self.grant.authority_id, self.grant.expires_at, operator_confirmed=True
        )
        self._allowed_modes = {heartbeat.mode}
        if not mode_rejection_test:
            self.adapter.set_target(initial, "prestream-ground-hold")
            self.transition("PRIMING")
            started = monotonic()
            while monotonic() - started < 1.5:
                self.pump()
        self._allowed_modes.add((6, 0))
        self.transition("SETTING_MODE")
        if mode_rejection_test:
            self.adapter.unavailable_mode_probe(f"unavailable-mode-{uuid4().hex}")
            self.wait_command("UNSUPPORTED_MODE")
            raise RuntimeError("unexpected unsupported mode; arming prohibited")
        self.adapter.offboard(f"offboard-{uuid4().hex}")
        self.wait_command("OFFBOARD")
        self._allowed_modes = {(6, 0)}
        self.transition("ARMING")
        self.adapter.arm(f"arm-{uuid4().hex}")
        self.wait_command("ARMED")
        elevated = LocalPosition(
            initial.north_m,
            initial.east_m,
            initial.down_m - self.limits.height_m,
            initial.origin_id,
        )
        self.move(elevated, "TAKEOFF")
        target = LocalPosition(
            initial.north_m + self.limits.north_m,
            initial.east_m + self.limits.east_m,
            elevated.down_m,
            initial.origin_id,
        )
        self.move(target, "MOVING")
        self.request = None
        self._allowed_modes.add((4, 6))
        self.transition("LANDING")
        self.adapter.land(f"land-{uuid4().hex}")
        self.wait_command("LAND")
        self.adapter.stop_stream()  # PX4 owns its observed AUTO.LAND descent now.
        self._allowed_modes = {(4, 6)}
        self.transition("WAIT_LANDED_DISARMED")
        deadline = monotonic() + self.limits.phase_timeout_s
        first_ground: float | None = None
        while monotonic() < deadline:
            self.pump()
            heartbeat = self.adapter.heartbeat
            if (
                heartbeat is not None
                and not heartbeat.armed
                and self.adapter.landed == 1
            ):
                stamp = min(self.adapter.heartbeat_at, self.adapter.landed_at)
                if first_ground is None:
                    first_ground = stamp
                if stamp - first_ground >= self.limits.dwell_s:
                    self.transition("COMPLETE")
                    self.emit("landed_disarmed_confirmed", dwell_s=self.limits.dwell_s)
                    return
            else:
                first_ground = None
        raise RuntimeError("landed/disarmed confirmation deadline")

    def abort(self, reason: str) -> None:
        self.adapter.abort(reason)
        self.authority.revoke()
        self.transition("ABORTED")
        self.emit(
            "scenario_failed",
            reason=reason,
            automatic_resumption=False,
            expected_px4_response="Offboard-loss Land after COM_OF_LOSS_T=0.5 if still in Offboard; otherwise operator-selected mode",
        )

    def observe_after_failure(self, seconds: float) -> None:
        """Bounded read-only recovery observation; never call flight poll/stream."""
        deadline = monotonic() + seconds
        reconnected = False
        while monotonic() < deadline:
            if (
                self.adapter.connection_state
                in (ConnectionState.LOST, ConnectionState.FAILED)
                and not reconnected
            ):
                self.adapter.open()
                reconnected = True
                self.emit("observation_only_reconnect", automatic_resumption=False)
            # Base telemetry update only: no flight commands or setpoint resumption.
            super(Px4SitlAdapter, self.adapter).update()
            if monotonic() - self._last_log > 0.2:
                self.emit("post_failure_observation", automatic_resumption=False)
                self._last_log = monotonic()
            sleep(0.01)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--authorize-sitl-flight",
        action="store_true",
        help="explicit operator action allowing mode, arm, bounded motion and landing in SITL",
    )
    parser.add_argument(
        "--mode-rejection-test",
        action="store_true",
        help="negative test: request unsupported custom mode 255; NEVER arm",
    )
    parser.add_argument("--bind-host", default="127.0.0.1")
    parser.add_argument("--bind-port", type=int, default=14540)
    parser.add_argument("--system-id", type=int, default=1)
    parser.add_argument("--component-id", type=int, default=1)
    parser.add_argument("--height", type=float, default=2)
    parser.add_argument("--north", type=float, default=2)
    parser.add_argument("--east", type=float, default=0)
    parser.add_argument("--radius", type=float, default=5)
    parser.add_argument("--ceiling", type=float, default=5)
    parser.add_argument("--xy-tolerance", type=float, default=0.3)
    parser.add_argument("--z-tolerance", type=float, default=0.2)
    parser.add_argument("--dwell", type=float, default=1)
    parser.add_argument("--observe-after-failure", type=float, default=20)
    parser.add_argument("--log", type=Path, default=None)
    args = parser.parse_args()
    try:
        if not IPv4Address(args.bind_host).is_loopback:
            raise ValueError("This SITL-only flight entry point binds loopback only")
        if (
            not isfinite(args.observe_after_failure)
            or not 0 <= args.observe_after_failure <= 60
        ):
            raise ValueError("Failure observation must be between 0 and 60 seconds")
        if args.mode_rejection_test and not args.authorize_sitl_flight:
            raise ValueError(
                "The negative flight-mode test also requires explicit authorization"
            )
        limits = SitlConfig(
            height_m=args.height,
            north_m=args.north,
            east_m=args.east,
            radius_m=args.radius,
            ceiling_m=args.ceiling,
            xy_tolerance_m=args.xy_tolerance,
            z_tolerance_m=args.z_tolerance,
            dwell_s=args.dwell,
        )
        telemetry = TelemetryConfig(
            bind_host=args.bind_host,
            bind_port=args.bind_port,
            system_id=args.system_id,
            component_id=args.component_id,
            poll_s=0.02,
            heartbeat_timeout_s=1,
            max_state_age_s=limits.telemetry_age_s,
        )
    except ValueError as exc:
        parser.error(str(exc))
    path = args.log or Path("artifacts/px4-sitl") / f"{uuid4().hex}.jsonl"
    # One host-local owner per selected vehicle, including alternate API ports.
    lock_path = Path(f"/tmp/arwen-px4-sitl-{args.system_id}-{args.component_id}.lock")
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("another ARWEN SITL controller owns this vehicle")
        events = JsonEvents(path, monotonic)
        print(f"EVENT_LOG={path}", flush=True)
        adapter = Px4SitlAdapter(
            FlightUdpTransport(telemetry), telemetry, limits, monotonic, events.write
        )
        scenario = SitlScenario(adapter, limits, events)
        events.write(
            "run_started",
            {
                "firmware": "PX4 v1.16.0",
                "simulator": "SIH quadx 10040",
                "operator_authorized": args.authorize_sitl_flight,
                "system_id": args.system_id,
                "component_id": args.component_id,
            },
        )
        try:
            adapter.open()
            scenario.run(
                operator_authorized=args.authorize_sitl_flight,
                mode_rejection_test=args.mode_rejection_test,
            )
            return 0
        except (Exception, KeyboardInterrupt) as exc:
            reason = f"{type(exc).__name__}: {exc}"
            print(reason, flush=True)
            scenario.abort(reason)
            try:
                scenario.observe_after_failure(args.observe_after_failure)
            except (Exception, KeyboardInterrupt) as observation_error:
                events.write("observation_failed", {"reason": str(observation_error)})
            return 1
        finally:
            adapter.stop_stream()
            scenario.authority.close()
            adapter.close()
            events.close()


if __name__ == "__main__":
    raise SystemExit(main())
