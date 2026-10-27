"""Health and system status endpoints."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter

from config.settings import get_settings

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health_check():
    cfg = get_settings()
    return {
        "status": "running",
        "version": "3.0.0-production",
        "factory": cfg.factory,
        "sources": {
            "mqtt": cfg.communication.mqtt.enabled,
            "http": cfg.communication.http.enabled and len(cfg.communication.http.pollers) > 0,
            "serial": cfg.communication.serial.enabled and len(cfg.communication.serial.ports) > 0,
        },
        "fault_detection": cfg.fault_detection.enabled,
        "llm_configured": bool(cfg.llm.api_key_env_var),
        "timestamp": datetime.now().isoformat(),
    }


@router.get("/health/detailed")
def detailed_health(acquisition_manager=None):
    """Include data acquisition source status if manager is available."""
    result = health_check()
    if acquisition_manager:
        result["acquisition"] = acquisition_manager.get_status()
    return result