"""
Fault Detection Engine — orchestrates rule evaluation.

Exposes the full decision pipeline to the LLM integration layer:
  - Raw reading + derived metrics
  - All rule evaluations (including non-triggered)
  - Final diagnosis with confidence ranking
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from config.settings import get_settings
from .rules import (
    ALL_RULES, Fault, RuleContext, compute_derived,
)
from storage.repositories import ReadingRepository

logger = logging.getLogger(__name__)


@dataclass
class DetectionResult:
    """Complete output of the fault detection engine for a single reading."""
    is_anomaly: bool
    primary_fault: Optional[Fault]
    all_faults: list[Fault]
    all_rule_evaluations: dict[str, Optional[Fault]]
    derived_metrics: dict[str, float]
    reading: dict
    context: RuleContext


class FaultDetectionEngine:
    """
    Configurable fault detection engine.

    Rules are enabled/disabled via configuration. Each reading is
    evaluated against all active rules, and results are ranked by
    confidence. The full evaluation trace is retained for LLM analysis.
    """

    def __init__(self) -> None:
        self._settings = get_settings()

    def _refresh_settings(self) -> None:
        """Allow hot-reload of thresholds and rule toggles."""
        self._settings = get_settings()

    def detect(self, reading: dict) -> DetectionResult:
        """
        Run full fault detection pipeline on a single reading.

        Args:
            reading: Dict with at least machine_name, power_kw, voltage_v,
                     current_amps, rated_load_kw, power_factor, frequency_hz.

        Returns:
            DetectionResult with full evaluation trace.
        """
        self._refresh_settings()

        fd_cfg = self._settings.fault_detection
        thresholds = self._settings.thresholds
        rules_cfg = fd_cfg.rules

        # Fetch recent readings for rolling context
        machine_name = reading.get("machine_name", "")
        recent = ReadingRepository.get_recent(machine_name, fd_cfg.rolling_window_size)
        ctx = RuleContext.from_recent(recent)

        # Compute derived electrical metrics
        derived = compute_derived(
            reading.get("power_kw", 0) or 0,
            reading.get("voltage_v", 0) or 0,
            reading.get("current_amps", 0) or 0,
            reading.get("rated_load_kw", 10),
        )

        # Evaluate every rule (even disabled ones get a None entry for transparency)
        all_evals: dict[str, Optional[Fault]] = {}
        triggered: list[Fault] = []

        for rule_id, rule_fn in ALL_RULES.items():
            enabled = rules_cfg.get(rule_id, False)
            try:
                fault = rule_fn(reading, ctx, thresholds, enabled)
            except Exception:
                logger.exception("Rule %s raised an exception", rule_id)
                fault = None
            all_evals[rule_id] = fault
            if fault is not None:
                # Attach derived metrics if the rule didn't compute its own
                if not fault.derived_metrics:
                    fault.derived_metrics = derived
                triggered.append(fault)

        # Sort by confidence descending
        triggered.sort(key=lambda f: f.confidence, reverse=True)

        primary = triggered[0] if triggered else None
        is_anomaly = primary is not None

        return DetectionResult(
            is_anomaly=is_anomaly,
            primary_fault=primary,
            all_faults=triggered,
            all_rule_evaluations=all_evals,
            derived_metrics=derived,
            reading=reading,
            context=ctx,
        )

    def detect_serializable(self, reading: dict) -> dict:
        """Return detection result as a plain dict (safe for JSON / DB storage)."""
        result = self.detect(reading)
        return {
            "is_anomaly": result.is_anomaly,
            "primary_fault": _fault_to_dict(result.primary_fault) if result.primary_fault else None,
            "all_faults": [_fault_to_dict(f) for f in result.all_faults],
            "all_rule_evaluations": {
                k: _fault_to_dict(v) if v else None for k, v in result.all_rule_evaluations.items()
            },
            "derived_metrics": result.derived_metrics,
            "context_summary": {
                "rolling_mean_power": result.context.rolling_mean_power,
                "rolling_std_power": result.context.rolling_std_power,
                "rolling_mean_voltage": result.context.rolling_mean_voltage,
                "prev_frequency": result.context.prev_frequency,
                "window_size": len(result.context.rolling_power),
            },
        }


def _fault_to_dict(fault: Fault) -> dict:
    return {
        "rule_id": fault.rule_id,
        "type": fault.type,
        "confidence": fault.confidence,
        "description": fault.description,
        "action": fault.action,
        "urgency": fault.urgency,
        "derived_metrics": fault.derived_metrics,
    }