"""Opt-in real PX4 SIH A–D harness. Never run against aircraft or existing PX4.

Starts a pinned local PX4 binary with fresh state for each case, routes real UDP
through a fault proxy and invokes the installed flight application. No vehicle
state or command acknowledgement is synthesized. All artifacts remain ignored.
"""

import argparse
import hashlib
import json
import os
import select
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from arwen.communication.mavlink import flight_wire_trace

PX4_COMMIT = "6ea3539157ca358c70a515878b77077af7d4611d"


def events(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    records: list[dict[str, object]] = []
    for line in path.read_text().splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            break  # A concurrent writer may not have finished its final line.
    return records


def command(build: Path, work: Path, *arguments: str) -> None:
    subprocess.run(
        [str(build / "bin" / "px4-commander"), *arguments],
        cwd=work,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        check=True,
        timeout=5,
    )


def check_case(
    case: str,
    records: list[dict[str, object]],
    wire: list[dict[str, object]],
    result: int,
    injected: bool,
) -> list[str]:
    failures: list[str] = []
    transitions = [
        row.get("state") for row in records if row.get("event") == "transition"
    ]
    revocations = [row for row in records if row.get("event") == "authority_revoked"]
    active = [
        row
        for row in wire
        if row.get("direction") == "controller_to_px4"
        and row.get("message") in ("COMMAND_LONG", "SET_POSITION_TARGET_LOCAL_NED")
    ]
    if not any(row.get("event") == "preflight_validated" for row in records):
        failures.append("preflight was not validated against real telemetry")
    if case == "nominal":
        if result != 0 or "COMPLETE" not in transitions:
            failures.append("nominal scenario did not complete")
        arrivals = [row for row in records if row.get("event") == "arrival_observed"]
        if {row.get("phase") for row in arrivals} != {"TAKEOFF", "MOVING"}:
            failures.append("both observed arrivals were not recorded")
        if not any(
            row.get("event") == "landed_disarmed_confirmed"
            and row.get("armed") is False
            and row.get("landed_state") == 1
            for row in records
        ):
            failures.append("landed and disarmed were not confirmed")
    else:
        if result == 0 or "ABORTED" not in transitions or "COMPLETE" in transitions:
            failures.append("negative scenario did not stop before completion")
        if not revocations:
            failures.append("authority revocation was not recorded")
        elif any(
            float(str(row["monotonic_s"])) > float(str(revocations[0]["monotonic_s"]))
            for row in active
        ):
            failures.append("active MAVLink output continued after revocation")
        if case == "takeover":
            if not injected or not any(
                row.get("direction") == "px4_to_controller"
                and row.get("mode") == [4, 3]
                and row.get("armed") is True
                and any(
                    event.get("state") == "MOVING"
                    and float(str(event["monotonic_s"]))
                    < float(str(row["monotonic_s"]))
                    for event in records
                )
                for row in wire
            ):
                failures.append("operator-selected Hold mode was not observed")
        if case == "link-loss":
            if not injected or not any(row.get("mode") == [4, 6] for row in wire):
                failures.append("PX4 Offboard-loss AUTO.LAND was not observed")
            if not any(
                row.get("event") == "post_failure_observation"
                and row.get("armed") is False
                and row.get("landed_state") == 1
                for row in records
            ):
                failures.append("landed/disarmed recovery was not observed")
            if not any(
                row.get("event") == "observation_only_reconnect" for row in records
            ):
                failures.append("read-only reconnection was not exercised")
        if case == "mode-rejection":
            if any(
                row.get("command") == 400
                or row.get("message") == "SET_POSITION_TARGET_LOCAL_NED"
                for row in active
            ):
                failures.append("mode-rejection case attempted arming or setpoints")
            ack_seen = any(
                row.get("message") == "COMMAND_ACK" and row.get("command") == 176
                for row in wire
            )
            unavailable = any(
                row.get("event") == "scenario_failed"
                and "UNSUPPORTED_MODE not observed" in str(row.get("reason"))
                for row in records
            )
            rejected = any(
                row.get("message") == "COMMAND_ACK"
                and row.get("command") == 176
                and row.get("result") not in (0, 5)
                for row in wire
            )
            if not ack_seen or not (unavailable or rejected):
                failures.append(
                    "neither rejected nor observed-unavailable mode was demonstrated"
                )
    return failures


def run_case(case: str, build: Path, output: Path) -> dict[str, object]:
    output.mkdir(parents=True)
    work = output / "px4-rootfs"
    work.mkdir()
    project = Path(__file__).resolve().parents[1]
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("PX4_PARAM_") and key != "PX4_SIM_SPEED_FACTOR"
    }
    environment.update(
        PX4_SYS_AUTOSTART="10040",
        PX4_SIM_MODEL="sihsim_quadx",
        PX4_PARAM_COM_RC_IN_MODE="4",
        PX4_PARAM_EKF2_MAG_TYPE="6",
        PX4_PARAM_COM_OF_LOSS_T="0.5",
        PX4_PARAM_COM_OBL_RC_ACT="4",
        PX4_PARAM_COM_DISARM_LAND="1",
    )
    event_path = output / "events.jsonl"
    wire_path = output / "wire.jsonl"
    wire_records: list[dict[str, object]] = []
    simulator: subprocess.Popen[bytes] | None = None
    scenario: subprocess.Popen[bytes] | None = None
    with (
        (output / "px4.log").open("wb") as simulator_log,
        (output / "scenario.log").open("wb") as scenario_log,
        wire_path.open("w") as trace,
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as proxy,
    ):
        proxy.bind(("127.0.0.1", 14540))
        proxy.setblocking(False)
        try:
            simulator = subprocess.Popen(
                [
                    str(build / "bin" / "px4"),
                    "-d",
                    str(build / "etc"),
                    "-w",
                    str(work),
                    "-s",
                    str(project / "tools" / "px4_sitl_startup.sh"),
                ],
                cwd=work,
                env=environment,
                stdout=simulator_log,
                stderr=subprocess.STDOUT,
            )
            arguments = [
                sys.executable,
                "-m",
                "arwen.applications.px4_sitl",
                "--authorize-sitl-flight",
                "--bind-port",
                "14541",
                "--log",
                str(event_path),
                "--observe-after-failure",
                "20",
            ]
            if case == "mode-rejection":
                arguments.append("--mode-rejection-test")
            scenario = subprocess.Popen(
                arguments, cwd=project, stdout=scenario_log, stderr=subprocess.STDOUT
            )
            deadline = time.monotonic() + 240
            moving_at: float | None = None
            injected = False
            cut_until = 0.0
            landed_by_operator = False
            next_scan = 0.0
            cached: list[dict[str, object]] = []
            while time.monotonic() < deadline:
                now = time.monotonic()
                if simulator.poll() is not None:
                    raise RuntimeError(
                        "PX4 exited; inspect px4.log (another instance may already own instance 0)"
                    )
                if scenario.poll() is not None:
                    break
                if now >= next_scan:
                    cached = events(event_path)
                    if moving_at is None and any(
                        row.get("state") == "MOVING" for row in cached
                    ):
                        moving_at = now
                    next_scan = now + 0.1
                if moving_at is not None and now - moving_at >= 0.5 and not injected:
                    if case == "takeover":
                        command(build, work, "mode", "auto:loiter")
                        injected = True
                    elif case == "link-loss":
                        cut_until = now + 3
                        injected = True
                if (
                    case == "takeover"
                    and injected
                    and not landed_by_operator
                    and moving_at is not None
                    and now - moving_at > 4
                ):
                    # Explicit second operator action, never an ARWEN recovery.
                    command(build, work, "land")
                    landed_by_operator = True
                readable, _, _ = select.select([proxy], [], [], 0.01)
                if readable:
                    data, peer = proxy.recvfrom(65535)
                    if peer == ("127.0.0.1", 14541):
                        direction, destination = (
                            "controller_to_px4",
                            ("127.0.0.1", 14580),
                        )
                    elif peer == ("127.0.0.1", 14580):
                        direction, destination = (
                            "px4_to_controller",
                            ("127.0.0.1", 14541),
                        )
                    else:
                        continue
                    blocked = now < cut_until
                    for row in flight_wire_trace(data):
                        row.update(
                            monotonic_s=time.monotonic(),
                            direction=direction,
                            blocked=blocked,
                        )
                        wire_records.append(row)
                        trace.write(json.dumps(row, allow_nan=False) + "\n")
                    trace.flush()
                    if not blocked:
                        proxy.sendto(data, destination)
            else:
                raise RuntimeError("acceptance harness deadline")
            result = scenario.wait(timeout=5)
            failures = check_case(
                case, events(event_path), wire_records, result, injected
            )
            summary: dict[str, object] = {
                "case": case,
                "status": "FAIL" if failures else "PASS",
                "failures": failures,
                "scenario_exit_code": result,
                "fault_injected": injected,
                "operator_landing_after_takeover": landed_by_operator,
                "px4_commit": PX4_COMMIT,
                "firmware": "v1.16.0",
                "simulator": "SIH quadx 10040",
                "events": str(event_path),
                "wire": str(wire_path),
                "events_sha256": hashlib.sha256(event_path.read_bytes()).hexdigest(),
                "wire_sha256": hashlib.sha256(wire_path.read_bytes()).hexdigest(),
            }
            (output / "result.json").write_text(json.dumps(summary, indent=2) + "\n")
            return summary
        finally:
            for process in (scenario, simulator):
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--px4-build", type=Path, required=True)
    parser.add_argument(
        "--case",
        choices=("all", "nominal", "takeover", "link-loss", "mode-rejection"),
        default="all",
    )
    parser.add_argument("--authorize-sitl-flight", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    if not args.authorize_sitl_flight:
        parser.error("this opt-in real flight harness requires --authorize-sitl-flight")
    build = args.px4_build.resolve()
    commit = subprocess.check_output(
        ["git", "-C", str(build), "rev-parse", "HEAD"], text=True
    ).strip()
    dirty = subprocess.check_output(
        ["git", "-C", str(build), "status", "--porcelain", "--untracked-files=no"],
        text=True,
    ).strip()
    if commit != PX4_COMMIT or dirty or not (build / "bin" / "px4").is_file():
        parser.error(
            "requires an unmodified v1.16.0 PX4 build from the selected commit"
        )
    output = (
        args.output or Path("artifacts/px4-sitl") / f"acceptance-{uuid4().hex}"
    ).resolve()
    cases = (
        ("nominal", "takeover", "link-loss", "mode-rejection")
        if args.case == "all"
        else (args.case,)
    )
    results = []
    for case in cases:
        print(f"Running real PX4 SIH case: {case}", flush=True)
        result = run_case(case, build, output / case)
        results.append(result)
        print(json.dumps(result), flush=True)
    (output / "summary.json").write_text(json.dumps(results, indent=2) + "\n")
    return 0 if all(result["status"] == "PASS" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
