"""Decoded wire records. All protocol knowledge stays in communication."""

from dataclasses import dataclass
from typing import Protocol


class WirePayload:
    """Marker for communication-owned decoded wire records."""


@dataclass(frozen=True, slots=True)
class Heartbeat(WirePayload):
    autopilot: int
    vehicle_type: int


@dataclass(frozen=True, slots=True)
class Position(WirePayload):
    boot_ms: int
    north: float
    east: float
    down: float


@dataclass(frozen=True, slots=True)
class Timesync(WirePayload):
    remote_ns: int
    echo_ns: int


@dataclass(frozen=True, slots=True)
class Packet:
    system: int
    component: int
    peer: tuple[str, int]
    payload: WirePayload


class Transport(Protocol):
    def open(self) -> None: ...
    def close(self) -> None: ...
    def receive(self, timeout_s: float) -> Packet | None: ...
    def select_peer(self, peer: tuple[str, int]) -> None: ...
    def request_timesync(self, stamp_ns: int) -> None: ...
