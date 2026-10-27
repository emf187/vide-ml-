"""
Diagnosis fusion service.

This is the one seam between fault_detection/ (rules), ml/ (learned
model), and llm_integration/ (chatbot). An engineer can rewrite any one
of those three packages without the others needing to change, as long
as they keep producing/consuming FusedDiagnosis.

Fusion logic, deliberately simple and inspectable (no black-box voting):
  - urgency  = the more severe of the rule engine's urgency and the ML
               layer's urgency (derived from anomaly_probability +
               hours_to_maintenance, using the same bands as
               MaintenanceService for consistency).
  - agreement = do the rule engine's primary fault and the ML model's
                predicted fault_type point at the same underlying issue?
                Disagreement isn't hidden — it's surfaced to the chatbot
                and API caller as a genuine signal worth a second look.
"""

from __future__ import annotations

from typing import Optional

from config.settings import get_settings
from fault_detection.engine import DetectionResult
from fusion.schema import FusedDiagnosis
from ml.schema import MLPrediction

_URGENCY_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def _ml_urgency(pred: MLPrediction, cfg) -> str:
    urg = cfg.maintenance.urgency
    if pred.hours_to_maintenance < urg.critical_hours or pred.anomaly_probability_next_window > 0.85:
        return "critical"
    if pred.hours_to_maintenance < urg.high_hours or pred.anomaly_probability_next_window > 0.6:
        return "high"
    if pred.hours_to_maintenance < urg.medium_hours or pred.anomaly_probability_next_window > 0.35:
        return "medium"
    if pred.anomaly_probability_next_window > 0.1:
        return "low"
    return "none"


def _rule_category(detection: DetectionResult) -> Optional[str]:
    """
    detection.primary_fault.rule_id is the SPECIFIC variant a rule
    returned (e.g. 'voltage_sag_critical', 'sensor_anomaly_power_gt_apparent')
    — several rule functions return different rule_id strings depending on
    severity. The ML model's fault_type is trained against ml/schema.py's
    RULE_IDS, which are the ALL_RULES *category* keys (e.g. 'voltage_sag'),
    not the specific variant. Comparing the two directly would report
    'disagree' on every critical-severity match. This finds which category
    key actually produced the primary fault, so the comparison is apples
    to apples.
    """
    if detection.primary_fault is None:
        return None
    for category, fault in detection.all_rule_evaluations.items():
        if fault is not None and fault.rule_id == detection.primary_fault.rule_id:
            return category
    return None


def fuse(detection: DetectionResult, ml_prediction: Optional[MLPrediction], machine_name: str) -> FusedDiagnosis:
    cfg = get_settings()

    rule_urgency = detection.primary_fault.urgency if detection.primary_fault else "none"

    if ml_prediction is None:
        overall_urgency = rule_urgency
        agreement = "no_ml_data"
    else:
        ml_urg = _ml_urgency(ml_prediction, cfg)
        overall_urgency = rule_urgency if _URGENCY_RANK[rule_urgency] >= _URGENCY_RANK[ml_urg] else ml_urg

        if detection.primary_fault is None and ml_prediction.fault_type == "normal":
            agreement = "agree"
        elif detection.primary_fault is None:
            agreement = "ml_only"
        elif ml_prediction.fault_type == "normal":
            agreement = "rules_only"
        elif ml_prediction.fault_type == _rule_category(detection):
            agreement = "agree"
        else:
            agreement = "disagree"

    summary = _build_summary(detection, ml_prediction, overall_urgency, agreement)

    return FusedDiagnosis(
        machine_name=machine_name,
        is_anomaly=overall_urgency != "none",
        primary_rule_fault=detection.primary_fault,
        all_rule_faults=detection.all_faults,
        all_rule_evaluations=detection.all_rule_evaluations,
        derived_metrics=detection.derived_metrics,
        ml_prediction=ml_prediction,
        overall_urgency=overall_urgency,
        agreement=agreement,
        summary=summary,
    )


def _build_summary(detection: DetectionResult, ml_prediction: Optional[MLPrediction], urgency: str, agreement: str) -> str:
    if urgency == "none":
        return "Normal operation — no faults detected by rules or ML."

    parts = []
    if detection.primary_fault:
        parts.append(f"Rules: {detection.primary_fault.type} ({detection.primary_fault.confidence}% confidence)")
    if ml_prediction:
        parts.append(
            f"ML: {ml_prediction.fault_type} ({ml_prediction.fault_type_confidence * 100:.0f}% confidence), "
            f"~{ml_prediction.hours_to_maintenance:.0f}h to maintenance"
        )
    if agreement == "disagree":
        parts.append("NOTE: rules and ML disagree on root cause — flag for engineer review.")

    return " | ".join(parts)
