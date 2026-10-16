"""Limits for the explicitly selected PX4 1.16.0 SIH quadrotor scenario."""

from dataclasses import dataclass
from math import hypot, isfinite


@dataclass(frozen=True, slots=True)
class SitlConfig:
    height_m: float = 2.0
    north_m: float = 2.0
    east_m: float = 0.0
    radius_m: float = 5.0
    ceiling_m: float = 5.0
    xy_tolerance_m: float = 0.3
    z_tolerance_m: float = 0.2
    dwell_s: float = 1.0
    telemetry_age_s: float = 0.6
    stream_interval_s: float = 0.05
    command_timeout_s: float = 6.0
    phase_timeout_s: float = 45.0
    total_timeout_s: float = 180.0

    def __post_init__(self) -> None:
        for value in (
            self.height_m,
            self.radius_m,
            self.ceiling_m,
            self.xy_tolerance_m,
            self.z_tolerance_m,
            self.dwell_s,
            self.telemetry_age_s,
            self.stream_interval_s,
            self.command_timeout_s,
            self.phase_timeout_s,
            self.total_timeout_s,
        ):
            if not isfinite(value) or value <= 0:
                raise ValueError("SITL limits must be finite and positive")
        if not all(isfinite(v) for v in (self.north_m, self.east_m)):
            raise ValueError("Target offsets must be finite")
        if not self.z_tolerance_m < self.height_m <= self.ceiling_m <= 10:
            raise ValueError("Height must exceed tolerance and fit a ceiling <=10 m")
        if (
            hypot(self.north_m, self.east_m) + self.xy_tolerance_m > self.radius_m
            or self.radius_m > 10
        ):
            raise ValueError("Target plus tolerance must fit radius <=10 m")
        if not 0.02 <= self.stream_interval_s <= 0.1:
            raise ValueError("Setpoint stream must run at 10–50 Hz")
        if not 0.2 <= self.telemetry_age_s <= 1:
            raise ValueError("Telemetry age must be between 0.2 and 1 second")
        if self.total_timeout_s > 300 or self.phase_timeout_s > self.total_timeout_s:
            raise ValueError("Scenario is limited to 300 seconds")
