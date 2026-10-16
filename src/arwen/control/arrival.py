"""Dwell-based arrival from distinct fresh measurements, not acknowledgements."""

from math import hypot

from arwen.config.sitl import SitlConfig
from arwen.vehicle.contracts import LocalPosition, VehicleState


class ArrivalTracker:
    def __init__(self, config: SitlConfig) -> None:
        self._config = config
        self._first: float | None = None
        self._last: float | None = None

    def reset(self) -> None:
        self._first = None
        self._last = None

    def update(self, state: VehicleState, target: LocalPosition, now: float) -> bool:
        position = state.position
        if (
            position is None
            or position.origin_id != target.origin_id
            or not state.position_is_fresh(now, self._config.telemetry_age_s)
            or hypot(position.north_m - target.north_m, position.east_m - target.east_m)
            > self._config.xy_tolerance_m
            or abs(position.down_m - target.down_m) > self._config.z_tolerance_m
        ):
            self.reset()
            return False
        stamp = state.measured_at
        if self._last is not None:
            if stamp <= self._last:
                return False
            if stamp - self._last >= self._config.telemetry_age_s:
                self.reset()
        if self._first is None:
            self._first = stamp
        self._last = stamp
        return stamp - self._first >= self._config.dwell_s
