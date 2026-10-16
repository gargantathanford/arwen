"""Additional decoded PX4 flight messages; never imported by vehicle contracts."""

from dataclasses import dataclass

from arwen.communication.messages import Heartbeat, WirePayload


@dataclass(frozen=True, slots=True)
class FlightHeartbeat(Heartbeat):
    base_mode: int
    custom_mode: int
    system_status: int

    @property
    def armed(self) -> bool:
        return bool(self.base_mode & 128)

    @property
    def mode(self) -> tuple[int, int]:
        return ((self.custom_mode >> 16) & 255, (self.custom_mode >> 24) & 255)


@dataclass(frozen=True, slots=True)
class LandedState(WirePayload):
    state: int


@dataclass(frozen=True, slots=True)
class Version(WirePayload):
    software: int
    capabilities: int


@dataclass(frozen=True, slots=True)
class Parameter(WirePayload):
    name: str
    value: float


@dataclass(frozen=True, slots=True)
class CommandAck(WirePayload):
    command: int
    result: int
    target_system: int
    target_component: int


@dataclass(frozen=True, slots=True)
class Estimator(WirePayload):
    boot_us: int
    flags: int


@dataclass(frozen=True, slots=True)
class Odometry(WirePayload):
    boot_us: int
    frame: int
    reset_counter: int
