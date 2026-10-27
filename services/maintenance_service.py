"""
Maintenance prediction service.

Uses configurable stress factors to adjust base maintenance intervals
based on actual equipment operating conditions.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from config.settings import get_settings
from storage.repositories import MachineRepository, ReadingRepository

logger = logging.getLogger(__name__)


class MaintenanceService:
    """Predicts maintenance needs based on equipment stress factors."""

    def __init__(self) -> None:
        self._cfg = get_settings()

    def predict(self, machine_name: str) -> dict:
        """
        Predict hours until next maintenance for a machine.

        Returns a dict with:
          - base_interval_hours
          - adjusted_interval_hours
          - current_hours_run
          - hours_remaining
          - days_remaining
          - recommended_maintenance_date
          - urgency (critical/high/medium/low)
          - stress_factors (breakdown of penalties)
        """
        machine = MachineRepository.get(machine_name)
        if not machine:
            return self._empty_prediction(machine_name)

        stats = ReadingRepository.get_machine_stats(machine_name)
        cfg = self._cfg.maintenance

        # Base interval from equipment category
        base_interval = self._cfg.get_maintenance_interval(machine["machine_type"])

        # Calculate stress penalties
        anomaly_penalty = stats["anomaly_count_30d"] * cfg.anomaly_penalty_hours
        overload_penalty = max(0.0, (stats["avg_load_factor"] - 80) * cfg.overload_penalty_hours_per_pct)
        voltage_penalty = max(0.0, (100 - stats["voltage_stability_score"]) * cfg.voltage_stress_penalty_per_point)
        thermal_penalty = min(cfg.max_thermal_penalty_hours, stats["i2t_thermal_stress_1h"] / 100.0)

        adjusted = base_interval - anomaly_penalty - overload_penalty - voltage_penalty - thermal_penalty
        remaining = max(0.0, adjusted - stats["total_hours_run"])
        days = remaining / 8.0
        recommended_date = (datetime.now() + timedelta(days=int(days))).strftime("%Y-%m-%d")

        # Urgency classification
        urg = cfg.urgency
        if remaining < urg.critical_hours:
            urgency = "critical"
        elif remaining < urg.high_hours:
            urgency = "high"
        elif remaining < urg.medium_hours:
            urgency = "medium"
        else:
            urgency = "low"

        return {
            "machine_name": machine_name,
            "base_interval_hours": base_interval,
            "adjusted_interval_hours": round(adjusted, 1),
            "current_hours_run": stats["total_hours_run"],
            "hours_remaining": round(remaining, 1),
            "days_remaining": round(days, 1),
            "recommended_maintenance_date": recommended_date,
            "urgency": urgency,
            "stress_factors": {
                "anomaly_penalty_hours": round(anomaly_penalty, 1),
                "overload_penalty_hours": round(overload_penalty, 1),
                "voltage_stress_penalty_hours": round(voltage_penalty, 1),
                "thermal_stress_penalty_hours": round(thermal_penalty, 1),
            },
            "input_stats": stats,
        }

    def _empty_prediction(self, machine_name: str) -> dict:
        return {
            "machine_name": machine_name,
            "base_interval_hours": 0,
            "adjusted_interval_hours": 0,
            "current_hours_run": 0,
            "hours_remaining": 0,
            "days_remaining": 0,
            "recommended_maintenance_date": "",
            "urgency": "unknown",
            "stress_factors": {},
            "input_stats": {},
        }