"""Runtime configuration query and update endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from config.settings import get_settings, reload_settings

router = APIRouter(prefix="/api/config", tags=["configuration"])


@router.get("")
def get_config():
    """Return current active configuration (secrets redacted)."""
    cfg = get_settings()
    return cfg.model_dump(exclude={"communication": {"mqtt": {"password"}}})


@router.get("/thresholds")
def get_thresholds():
    cfg = get_settings()
    return cfg.thresholds.model_dump()


@router.get("/rules")
def get_rules():
    cfg = get_settings()
    return {
        "enabled": cfg.fault_detection.enabled,
        "rules": cfg.fault_detection.rules,
        "rolling_window_size": cfg.fault_detection.rolling_window_size,
    }


@router.post("/reload")
def reload():
    """Force re-read configuration from YAML file."""
    new_cfg = reload_settings()
    return {"status": "reloaded", "factory": new_cfg.factory}