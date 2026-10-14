"""Terminal passive telemetry monitor: python -m arwen.applications.telemetry."""

import argparse
import sys
from time import monotonic

from arwen.communication.mavlink import MissingMavlink, UdpTransport
from arwen.communication.px4 import ConnectionState, Px4Adapter
from arwen.config.telemetry import TelemetryConfig


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only PX4 NED telemetry monitor")
    parser.add_argument("--bind-host", default="127.0.0.1")
    parser.add_argument("--bind-port", type=int, default=14540)
    parser.add_argument("--system-id", type=int, default=1)
    parser.add_argument("--component-id", type=int, default=1)
    parser.add_argument("--poll", type=float, default=0.1)
    parser.add_argument("--display-interval", type=float, default=0.5)
    parser.add_argument("--connection-timeout", type=float, default=10)
    parser.add_argument("--heartbeat-timeout", type=float, default=3)
    parser.add_argument("--max-state-age", type=float, default=1)
    args = parser.parse_args()
    try:
        config = TelemetryConfig(
            bind_host=args.bind_host,
            bind_port=args.bind_port,
            system_id=args.system_id,
            component_id=args.component_id,
            poll_s=args.poll,
            display_interval_s=args.display_interval,
            connection_timeout_s=args.connection_timeout,
            heartbeat_timeout_s=args.heartbeat_timeout,
            max_state_age_s=args.max_state_age,
        )
    except ValueError as exc:
        parser.error(str(exc))
    adapter = Px4Adapter(UdpTransport(config), config, monotonic)
    try:
        adapter.open()
        next_display = 0.0
        while True:
            adapter.update()
            now = monotonic()
            health = adapter.connection_state
            if now >= next_display:
                state = adapter.snapshot()
                position = state.position if state is not None else None
                fresh = (
                    state.position_is_fresh(now, config.max_state_age_s)
                    if state
                    else False
                )
                print(
                    f"health={health.value} timing={adapter.timing_quality.value} "
                    f"position={position} fresh={fresh}",
                    flush=True,
                )
                next_display = now + config.display_interval_s
            if health in (ConnectionState.LOST, ConnectionState.FAILED):
                print(adapter.failure, file=sys.stderr)
                return 1
    except KeyboardInterrupt:
        return 0
    except (OSError, MissingMavlink, RuntimeError) as exc:
        print(f"Telemetry monitor: {exc}", file=sys.stderr)
        return 1
    finally:
        adapter.close()


if __name__ == "__main__":
    raise SystemExit(main())
