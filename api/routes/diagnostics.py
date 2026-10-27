"""
Diagnostics endpoints — expose fault detection engine, ML layer, fusion,
and LLM analysis.

This is the deep integration layer: the LLM sees the full fault detection
output, the ML prediction (when available), the rule/ML agreement, and
live data to produce expert analysis.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from fault_detection.engine import FaultDetectionEngine
from fusion.fusion_service import fuse
from llm_integration.client import LLMClient
from ml.inference_service import get_inference_service
from ml.labels import confirm as confirm_critical_event
from storage.repositories import CriticalEventRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])


class AnalyzeReadingInput(BaseModel):
    machine_name: str
    machine_type: str = "custom"
    rated_load_kw: float = 10.0
    voltage_v: float | None = None
    current_amps: float | None = None
    power_kw: float | None = None
    power_factor: float = 1.0
    frequency_hz: float = 50.0
    use_llm: bool = False
    force_ml: bool = False  # bypass batching for this one-off analysis call


class ChatInput(BaseModel):
    message: str


class ConfirmCriticalInput(BaseModel):
    confirmed: bool
    technician: str = "unknown"
    notes: str = ""


@router.post("/analyze")
def analyze_reading(body: AnalyzeReadingInput):
    """
    Run fault detection + ML + fusion on a reading without storing it.
    Optionally invokes the LLM for deep analysis.
    """
    engine = FaultDetectionEngine()
    reading = body.model_dump()

    detection_result = engine.detect(reading)
    serializable = engine.detect_serializable(reading)

    ml_prediction = None
    if body.force_ml:
        # /analyze is a diagnostic/testing endpoint — force_ml bypasses the
        # batching interval so callers get an immediate ML opinion.
        svc = get_inference_service()
        ml_prediction = svc.maybe_predict(reading, detection_result, body.machine_name) \
            if svc._model is not None else None
        if ml_prediction is None and svc._model is not None:
            # batching skipped it — force a one-off prediction directly
            from ml.explainer import explain_prediction
            from ml.features import build_feature_vector
            from ml.schema import MLPrediction
            x = build_feature_vector(reading, detection_result, body.machine_type)
            raw = svc._model.predict_one(x)
            explanation = explain_prediction(svc._model.fault_type_model, x)
            ml_prediction = MLPrediction(
                machine_name=body.machine_name, model_version=svc._model.version,
                feature_importance=explanation["feature_importance"], top_factors=explanation["top_factors"],
                **raw,
            )

    fused = fuse(detection_result, ml_prediction, body.machine_name)

    response: dict = {
        "is_anomaly": detection_result.is_anomaly,
        "derived_metrics": detection_result.derived_metrics,
        "all_faults": serializable["all_faults"],
        "all_rule_evaluations": serializable["all_rule_evaluations"],
        "primary_fault": serializable["primary_fault"],
        "ml_prediction": ml_prediction.__dict__ if ml_prediction else None,
        "fused_diagnosis": {
            "overall_urgency": fused.overall_urgency,
            "agreement": fused.agreement,
            "summary": fused.summary,
        },
    }

    if body.use_llm:
        llm = LLMClient()
        llm_result = llm.analyze_reading(reading, detection_result, fused)
        response["llm_analysis"] = llm_result.get("response", "")
        if "error" in llm_result:
            response["llm_error"] = llm_result["error"]

    return response


@router.post("/chat")
def chat(body: ChatInput):
    """General LLM chat with automatic system context injection."""
    llm = LLMClient()
    return llm.chat(body.message)


@router.get("/critical-events")
def list_critical_events(limit: int = 50):
    """Pending critical-event flags awaiting engineer confirmation."""
    return CriticalEventRepository.get_pending(limit=limit)


@router.post("/critical-events/{event_id}/confirm")
def confirm_event(event_id: int, body: ConfirmCriticalInput):
    """
    Engineer confirms or dismisses a rule-engine critical flag. Only
    confirmed events become trusted training labels — nothing here is
    ever auto-labeled as a failure.
    """
    try:
        confirm_critical_event(event_id, confirmed=body.confirmed, technician=body.technician, notes=body.notes)
        return {"status": "ok", "event_id": event_id, "confirmed": body.confirmed}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
