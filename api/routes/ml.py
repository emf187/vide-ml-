"""ML layer status and prediction query endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from config.settings import get_settings
from ml.inference_service import get_inference_service
from ml.registry import ModelRegistry
from storage.repositories import MLPredictionRepository

router = APIRouter(prefix="/api/ml", tags=["ml"])


@router.get("/status")
def ml_status():
    """Whether the ML layer is enabled and which model version is active."""
    cfg = get_settings()
    registry = ModelRegistry(cfg.ml.model_dir)
    svc = get_inference_service()
    return {
        "enabled": cfg.ml.enabled,
        "current_version": registry.current_version(),
        "loaded": svc._model is not None,
        "batch_interval_readings": cfg.ml.batch_interval_readings,
    }


@router.get("/predictions/{machine_name}")
def latest_prediction(machine_name: str):
    pred = MLPredictionRepository.get_latest(machine_name)
    if not pred:
        raise HTTPException(status_code=404, detail=f"No ML predictions yet for '{machine_name}'")
    return pred


@router.get("/predictions/{machine_name}/history")
def prediction_history(machine_name: str, limit: int = 50):
    return MLPredictionRepository.get_history(machine_name, limit=limit)
