"""
FusedDiagnosis is the ONLY object the LLM chatbot and API responses read
from. Neither the rule engine nor the ML layer is consumed directly
downstream of this point — that's what keeps them swappable in
isolation (see architecture note in fusion_service.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from fault_detection.rules import Fault
from ml.schema import MLPrediction


@dataclass
class FusedDiagnosis:
    machine_name: str
    is_anomaly: bool

    # rule engine side — unchanged from fault_detection.engine.DetectionResult
    primary_rule_fault: Optional[Fault]
    all_rule_faults: list[Fault]
    all_rule_evaluations: dict[str, Optional[Fault]]
    derived_metrics: dict[str, float]

    # ML side — None if the ML layer hasn't produced a prediction this cycle
    # (batched inference — most readings won't have a fresh one)
    ml_prediction: Optional[MLPrediction]

    # fused view
    overall_urgency: str          # "critical" | "high" | "medium" | "low" | "none"
    agreement: str                 # "agree" | "disagree" | "ml_only" | "rules_only" | "no_ml_data"
    summary: str
    stress_factors: dict = field(default_factory=dict)
