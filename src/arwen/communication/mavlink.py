"""Lazy pymavlink boundary, UDP decoding and TIMESYNC-only transmission.

Generated pymavlink classes lack annotations. Any is confined to this file;
validated records cross the boundary. No mavutil connection or auto-send logic.
"""

import socket
import struct
from collections import deque
from collections.abc import Callable
from importlib import import_module
from time import monotonic
from typing import Any

from arwen.communication.messages import Heartbeat, Packet, Position, Timesync
from arwen.config.telemetry import TelemetryConfig


class MissingMavlink(RuntimeError):
    """The optional MAVLink dependency is unavailable."""


def _dialect() -> Any:  # noqa: ANN401 - generated untyped dialect boundary
    try:
        return import_module("pymavlink.dialects.v20.common")
    except ImportError as exc:
        raise MissingMavlink(
            "Install MAVLink support: pip install '.[mavlink]'"
        ) from exc


class Codec:
    def __init__(self) -> None:
        self._module = _dialect()
        self._encoder = self._module.MAVLink(None, srcSystem=255, srcComponent=191)

    def decode(self, data: bytes, peer: tuple[str, int]) -> list[Packet]:
        parser = self._module.MAVLink(None)
        parser.robust_parsing = True
        result: list[Packet] = []
        try:
            messages = parser.parse_buffer(data) or []
        except (ValueError, self._module.MAVError):
            return result
        for message in messages:
            payload: Heartbeat | Position | Timesync
            kind = message.get_type()
            if kind == "HEARTBEAT":
                payload = Heartbeat(int(message.autopilot), int(message.type))
            elif kind == "LOCAL_POSITION_NED":
                payload = Position(
                    int(message.time_boot_ms),
                    float(message.x),
                    float(message.y),
                    float(message.z),
                )
            elif kind == "TIMESYNC":
                payload = Timesync(int(message.tc1), int(message.ts1))
            else:
                continue
            result.append(
                Packet(
                    int(message.get_srcSystem()),
                    int(message.get_srcComponent()),
                    peer,
                    payload,
                )
            )
        return result

    def encode_housekeeping(self, kind: str, stamp_ns: int) -> bytes:
        if kind != "TIMESYNC" or not 0 < stamp_ns < 2**63:
            raise ValueError("Only positive-stamped TIMESYNC requests are allowed")
        message = self._module.MAVLink_timesync_message(0, stamp_ns)
        data = bytes(message.pack(self._encoder))
        self._encoder.seq = (self._encoder.seq + 1) % 256
        return data


class UdpTransport:
    def __init__(
        self, config: TelemetryConfig, *, codec_factory: Callable[[], Codec] = Codec
    ) -> None:
        self._config = config
        self._codec_factory = codec_factory
        self._socket: socket.socket | None = None
        self._codec: Codec | None = None
        self._peer: tuple[str, int] | None = None
        self._queue: deque[Packet] = deque()

    def open(self) -> None:
        if self._socket is not None:
            raise RuntimeError("Transport is already open")
        codec = self._codec_factory()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((self._config.bind_host, self._config.bind_port))
        except OSError:
            sock.close()
            raise
        self._codec = codec
        self._socket = sock
        self._peer = None
        self._queue.clear()

    def close(self) -> None:
        if self._socket is not None:
            self._socket.close()
        self._socket = None
        self._codec = None
        self._peer = None
        self._queue.clear()

    def select_peer(self, peer: tuple[str, int]) -> None:
        self._peer = peer

    def receive(self, timeout_s: float) -> Packet | None:
        if not 0 <= timeout_s <= 1:
            raise ValueError("Receive timeout must be between zero and one second")
        if self._socket is None or self._codec is None:
            raise RuntimeError("Transport is closed")
        if self._queue:
            return self._queue.popleft()
        deadline = monotonic() + timeout_s
        # Unknown/malformed frames are traffic, not an empty socket. Skipping
        # only one per update can leave real telemetry stuck behind a backlog.
        # Both elapsed wait and datagrams inspected are bounded.
        for _ in range(64):
            self._socket.settimeout(max(0.0, deadline - monotonic()))
            try:
                data, peer = self._socket.recvfrom(65535)
            except (TimeoutError, BlockingIOError):
                return None
            if self._peer is None or peer == self._peer:
                self._queue.extend(self._codec.decode(data, peer))
            if self._queue:
                return self._queue.popleft()
        return None

    def request_timesync(self, stamp_ns: int) -> None:
        if self._socket is None or self._codec is None or self._peer is None:
            raise RuntimeError("No selected UDP peer")
        self._socket.sendto(
            self._codec.encode_housekeeping("TIMESYNC", stamp_ns), self._peer
        )


class FlightCodec(Codec):
    """Opt-in SITL codec. Passive Codec retains its TIMESYNC-only output API."""

    def decode(self, data: bytes, peer: tuple[str, int]) -> list[Packet]:
        from arwen.communication.flight_messages import (
            CommandAck,
            Estimator,
            FlightHeartbeat,
            LandedState,
            Odometry,
            Parameter,
            Version,
        )
        from arwen.communication.messages import WirePayload

        result = [
            packet
            for packet in super().decode(data, peer)
            if not isinstance(packet.payload, Heartbeat)
        ]
        parser = self._module.MAVLink(None)
        parser.robust_parsing = True
        try:
            messages = parser.parse_buffer(data) or []
        except (ValueError, self._module.MAVError):
            return result
        for message in messages:
            payload: WirePayload
            kind = message.get_type()
            if kind == "HEARTBEAT":
                payload = FlightHeartbeat(
                    int(message.autopilot),
                    int(message.type),
                    int(message.base_mode),
                    int(message.custom_mode),
                    int(message.system_status),
                )
            elif kind == "EXTENDED_SYS_STATE":
                payload = LandedState(int(message.landed_state))
            elif kind == "AUTOPILOT_VERSION":
                payload = Version(
                    int(message.flight_sw_version), int(message.capabilities)
                )
            elif kind == "COMMAND_ACK":
                payload = CommandAck(
                    int(message.command),
                    int(message.result),
                    int(message.target_system),
                    int(message.target_component),
                )
            elif kind == "PARAM_VALUE":
                value = float(message.param_value)
                if int(message.param_type) == 6:  # PX4 byte-wise INT32 encoding.
                    value = float(struct.unpack("<i", struct.pack("<f", value))[0])
                elif int(message.param_type) != 9:
                    continue
                payload = Parameter(str(message.param_id).rstrip("\x00"), value)
            elif kind == "ODOMETRY":
                payload = Odometry(
                    int(message.time_usec),
                    int(message.frame_id),
                    int(message.reset_counter),
                )
            elif kind == "ESTIMATOR_STATUS":
                payload = Estimator(int(message.time_usec), int(message.flags))
            else:
                continue
            result.append(
                Packet(
                    int(message.get_srcSystem()),
                    int(message.get_srcComponent()),
                    peer,
                    payload,
                )
            )
        return result

    def _pack(self, message: Any) -> bytes:  # noqa: ANN401 - generated dialect boundary
        data = bytes(message.pack(self._encoder))
        self._encoder.seq = (self._encoder.seq + 1) % 256
        return data

    def command(
        self, system: int, component: int, command: int, parameters: tuple[float, ...]
    ) -> bytes:
        if command not in (176, 400, 21, 512) or len(parameters) != 7:
            raise ValueError("SITL command allowlist violation")
        if command == 176 and parameters not in (
            (1, 6, 0, 0, 0, 0, 0),
            (1, 255, 0, 0, 0, 0, 0),
        ):
            raise ValueError(
                "Only OFFBOARD or the explicit unsupported-mode probe is permitted"
            )
        if command == 400 and parameters != (1, 0, 0, 0, 0, 0, 0):
            raise ValueError("Only normal arm is permitted; force/disarm forbidden")
        if command == 512 and parameters != (148, 0, 0, 0, 0, 0, 0):
            raise ValueError("Only AUTOPILOT_VERSION may be requested")
        if command == 21 and parameters != (0, 0, 0, 0, 0, 0, 0):
            raise ValueError("Only land at current position is permitted")
        return self._pack(
            self._module.MAVLink_command_long_message(
                system, component, command, 0, *parameters
            )
        )

    def parameter_read(self, system: int, component: int, name: str) -> bytes:
        if name not in (
            "SYS_AUTOSTART",
            "EKF2_MAG_TYPE",
            "COM_RC_IN_MODE",
            "COM_OF_LOSS_T",
            "COM_OBL_RC_ACT",
            "COM_DISARM_LAND",
        ):
            raise ValueError("Unexpected parameter read")
        return self._pack(
            self._module.MAVLink_param_request_read_message(
                system, component, name.encode("ascii"), -1
            )
        )

    def setpoint(
        self,
        system: int,
        component: int,
        boot_ms: int,
        north: float,
        east: float,
        down: float,
    ) -> bytes:
        from math import isfinite

        if (
            not all(isfinite(value) for value in (north, east, down))
            or not 0 <= boot_ms < 2**32
        ):
            raise ValueError("Invalid local position setpoint")
        return self._pack(
            self._module.MAVLink_set_position_target_local_ned_message(
                boot_ms,
                system,
                component,
                1,
                3576,
                north,
                east,
                down,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
            )
        )


class FlightUdpTransport(UdpTransport):
    """Distinct opt-in output surface; never used by the passive monitor."""

    def __init__(self, config: TelemetryConfig) -> None:
        super().__init__(config, codec_factory=FlightCodec)

    def _send(self, data: bytes) -> None:
        if self._socket is None or self._peer is None:
            raise RuntimeError("No selected SITL peer")
        self._socket.sendto(data, self._peer)

    def _flight_codec(self) -> FlightCodec:
        if not isinstance(self._codec, FlightCodec):
            raise RuntimeError("Flight transport is closed")
        return self._codec

    def command(self, command: int, parameters: tuple[float, ...]) -> None:
        self._send(
            self._flight_codec().command(
                self._config.system_id, self._config.component_id, command, parameters
            )
        )

    def parameter_read(self, name: str) -> None:
        self._send(
            self._flight_codec().parameter_read(
                self._config.system_id, self._config.component_id, name
            )
        )

    def setpoint(self, boot_ms: int, north: float, east: float, down: float) -> None:
        self._send(
            self._flight_codec().setpoint(
                self._config.system_id,
                self._config.component_id,
                boot_ms,
                north,
                east,
                down,
            )
        )


def flight_wire_trace(data: bytes) -> list[dict[str, object]]:
    """Decode evidence for the opt-in UDP acceptance proxy, with no outbound I/O."""
    module = _dialect()
    parser = module.MAVLink(None)
    parser.robust_parsing = True
    try:
        messages = parser.parse_buffer(data) or []
    except (ValueError, module.MAVError):
        return []
    records: list[dict[str, object]] = []
    for message in messages:
        kind = str(message.get_type())
        record: dict[str, object] = {
            "message": kind,
            "system_id": int(message.get_srcSystem()),
            "component_id": int(message.get_srcComponent()),
        }
        if kind == "HEARTBEAT":
            record.update(
                mode=[
                    (int(message.custom_mode) >> 16) & 255,
                    (int(message.custom_mode) >> 24) & 255,
                ],
                armed=bool(int(message.base_mode) & 128),
            )
        elif kind == "EXTENDED_SYS_STATE":
            record["landed_state"] = int(message.landed_state)
        elif kind in ("COMMAND_LONG", "COMMAND_ACK"):
            record["command"] = int(message.command)
            if kind == "COMMAND_ACK":
                record["result"] = int(message.result)
        elif kind == "SET_POSITION_TARGET_LOCAL_NED":
            record["target_ned_m"] = [
                float(message.x),
                float(message.y),
                float(message.z),
            ]
        else:
            continue
        records.append(record)
    return records
