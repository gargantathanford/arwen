"""Explicit UDP monitor settings, independent of protocol libraries."""

from dataclasses import dataclass
from ipaddress import IPv4Address
from math import isfinite


@dataclass(frozen=True, slots=True)
class TelemetryConfig:
    bind_host: str = "127.0.0.1"
    bind_port: int = 14540
    system_id: int = 1
    component_id: int = 1
    connection_timeout_s: float = 10.0
    heartbeat_timeout_s: float = 3.0
    poll_s: float = 0.1
    sync_interval_s: float = 1.0
    sync_max_rtt_s: float = 0.1
    sync_valid_s: float = 5.0
    display_interval_s: float = 0.5
    max_state_age_s: float = 1.0

    def __post_init__(self) -> None:
        IPv4Address(self.bind_host)
        for name, value, minimum, maximum in (
            ("bind_port", self.bind_port, 1, 65535),
            ("system_id", self.system_id, 1, 255),
            ("component_id", self.component_id, 1, 255),
        ):
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"{name} outside supported range")
        for duration in (
            self.connection_timeout_s,
            self.heartbeat_timeout_s,
            self.poll_s,
            self.sync_interval_s,
            self.sync_max_rtt_s,
            self.sync_valid_s,
            self.display_interval_s,
            self.max_state_age_s,
        ):
            if not isfinite(duration) or duration <= 0:
                raise ValueError("timing settings must be finite and positive")
        if self.poll_s > 1 or self.poll_s > self.heartbeat_timeout_s:
            raise ValueError("poll must be <= 1 second and heartbeat timeout")
        if self.sync_interval_s < self.poll_s:
            raise ValueError("sync interval must be >= poll duration")
        if self.sync_max_rtt_s > self.sync_valid_s:
            raise ValueError("RTT bound cannot exceed alignment lifetime")
