"""Lazy pymavlink boundary, UDP decoding and TIMESYNC-only transmission.

Generated pymavlink classes lack annotations. Any is confined to this file;
validated records cross the boundary. No mavutil connection or auto-send logic.
"""

import socket
from collections import deque
from importlib import import_module
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
    def __init__(self, config: TelemetryConfig) -> None:
        self._config = config
        self._socket: socket.socket | None = None
        self._codec: Codec | None = None
        self._peer: tuple[str, int] | None = None
        self._queue: deque[Packet] = deque()

    def open(self) -> None:
        if self._socket is not None:
            raise RuntimeError("Transport is already open")
        codec = Codec()
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
        self._socket.settimeout(timeout_s)
        try:
            data, peer = self._socket.recvfrom(65535)
        except (TimeoutError, BlockingIOError):
            return None
        if self._peer is None or peer == self._peer:
            self._queue.extend(self._codec.decode(data, peer))
        return self._queue.popleft() if self._queue else None

    def request_timesync(self, stamp_ns: int) -> None:
        if self._socket is None or self._codec is None or self._peer is None:
            raise RuntimeError("No selected UDP peer")
        self._socket.sendto(
            self._codec.encode_housekeeping("TIMESYNC", stamp_ns), self._peer
        )
