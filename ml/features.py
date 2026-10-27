"""
Feature engineering — turns a reading + DetectionResult (from
fault_detection.engine) into the fixed-width vector XGBoost expects.

This is imported by BOTH ml/inference_service.py (live) and
training/feature_pipeline.py (offline) so the two paths can never drift.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from fault_detection.engine import DetectionResult
from ml.schema import FEATURE_ORDER, MACHINE_TYPES, RULE_IDS


def build_feature_vector(
    reading: dict[str, Any],
    detection: DetectionResult,
    machine_type: str,
) -> np.ndarray:
    """
    Build one row of features. Order must match FEATURE_ORDER exactly —
    do not reorder dict access below without updating ml/schema.py.
    """
    dm = detection.derived_metrics
    ctx = detection.context

    values: dict[str, float] = {
        "voltage_v": reading.get("voltage_v", 0) or 0,
        "current_amps": reading.get("current_amps", 0) or 0,
        "power_kw": reading.get("power_kw", 0) or 0,
        "power_factor": reading.get("power_factor", 1.0) or 1.0,
        "frequency_hz": reading.get("frequency_hz", 50.0) or 50.0,
        "rated_load_kw": reading.get("rated_load_kw", 10.0) or 10.0,
        "apparent_power_kva": dm.get("apparent_power_kva", 0.0),
        "reactive_power_kvar": dm.get("reactive_power_kvar", 0.0),
        "load_factor_pct": dm.get("load_factor_pct", 0.0),
        "rolling_mean_power": ctx.rolling_mean_power,
        "rolling_std_power": ctx.rolling_std_power,
        "rolling_mean_voltage": ctx.rolling_mean_voltage,
        "prev_frequency": ctx.prev_frequency,
    }

    for t in MACHINE_TYPES:
        values[f"machine_type_{t}"] = 1.0 if machine_type == t else 0.0

    confidences = []
    for rule_id in RULE_IDS:
        fault = detection.all_rule_evaluations.get(rule_id)
        conf = float(fault.confidence) if fault is not None else 0.0
        values[f"rule_conf_{rule_id}"] = conf
        confidences.append(conf)

    triggered = [c for c in confidences if c > 0]
    values["any_rule_triggered"] = 1.0 if triggered else 0.0
    values["num_rules_triggered"] = float(len(triggered))
    values["max_rule_confidence"] = float(max(confidences)) if confidences else 0.0

    return np.array([values[f] for f in FEATURE_ORDER], dtype=np.float32)


def feature_names() -> list[str]:
    """Convenience accessor so ml/explainer.py can label SHAP values."""
    return list(FEATURE_ORDER)
