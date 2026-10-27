"""
Data contracts for the ML layer.

FEATURE_ORDER is the single source of truth for what a feature vector
contains and in what order. training/feature_pipeline.py imports this
same list, so training and inference can never silently drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Machine types must match config.yaml's equipment.categories keys.
MACHINE_TYPES = ["motor", "pump", "compressor", "generator", "conveyor", "hvac", "custom"]

# Rule ids must match fault_detection.rules.ALL_RULES keys. Order matters —
# this is how "all rule evaluations" get turned into fixed-width ML features.
RULE_IDS = [
    "sensor_anomaly",
    "voltage_sag",
    "voltage_swell",
    "frequency_instability",
    "mechanical_overload",
    "low_power_factor",
    "excessive_reactive_power",
    "mechanical_degradation",
    "underload",
]

FEATURE_ORDER = [
    # raw reading
    "voltage_v", "current_amps", "power_kw", "power_factor", "frequency_hz",
    "rated_load_kw",
    # derived metrics (from fault_detection.rules.compute_derived)
    "apparent_power_kva", "reactive_power_kvar", "load_factor_pct",
    # rolling context (from fault_detection.rules.RuleContext)
    "rolling_mean_power", "rolling_std_power", "rolling_mean_voltage", "prev_frequency",
    # machine type, one-hot
    *[f"machine_type_{t}" for t in MACHINE_TYPES],
    # rule engine outputs, stacked as features: confidence per rule (0 if not triggered)
    *[f"rule_conf_{r}" for r in RULE_IDS],
    # any rule triggered at all, and how many
    "any_rule_triggered", "num_rules_triggered", "max_rule_confidence",
]

FAULT_TYPE_CLASSES = ["normal"] + RULE_IDS


@dataclass
class MLPrediction:
    """Output of a single inference pass for one machine."""
    machine_name: str
    hours_to_maintenance: float
    anomaly_probability_next_window: float
    fault_type: str
    fault_type_confidence: float
    model_version: str
    feature_importance: dict[str, float] = field(default_factory=dict)
    top_factors: list[str] = field(default_factory=list)
    is_confident: bool = True
    timestamp: Optional[str] = None
