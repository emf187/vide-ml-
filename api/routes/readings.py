"""Sensor reading ingestion and query endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.reading_service import ReadingService
from storage.repositories import ReadingRepository

router = APIRouter(prefix="/api/readings", tags=["readings"])


class ReadingInput(BaseModel):
    """Accepts readings from any source — all fields optional except machine_name."""
    machine_name: str
    machine_type: str = "custom"
    location: str = "unknown"
    source_type: str = "api"
    rated_load_kw: float = 10.0
    voltage_v: float | None = None
    current_amps: float | None = None
    power_kw: float | None = None
    power_factor: float = 1.0
    frequency_hz: float = 50.0
    energy_kwh: float | None = None
    temperature_c: float | None = None
    vibration_mm_s: float | None = None
    acceleration_g: float | None = None
    pressure_bar: float | None = None
    flow_lpm: float | None = None


class BatchReadingInput(BaseModel):
    readings: list[ReadingInput] = Field(..., max_length=100)


@router.post("")
def ingest_reading(body: ReadingInput):
    """Ingest a single sensor reading — runs fault detection and stores."""
    svc = ReadingService()
    try:
        result = svc.process(body.model_dump())
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/batch")
def ingest_batch(body: BatchReadingInput):
    """Ingest multiple readings in one request."""
    svc = ReadingService()
    results = []
    errors = []
    for r in body.readings:
        try:
            result = svc.process(r.model_dump())
            results.append(result)
        except Exception as e:
            errors.append({"machine": r.machine_name, "error": str(e)})
    return {"processed": len(results), "errors": len(errors), "results": results, "error_details": errors}


@router.get("/recent/{machine_name}")
def get_recent_readings(machine_name: str, limit: int = 50):
    readings = ReadingRepository.get_machine_history(machine_name, limit=limit)
    if not readings:
        raise HTTPException(status_code=404, detail=f"No readings found for '{machine_name}'")
    return readings


@router.get("/heatmap")
def get_heatmap():
    return ReadingRepository.get_heatmap()


@router.get("/daily-trend")
def get_daily_trend():
    return ReadingRepository.get_daily_trend()


@router.get("/summary")
def get_summary():
    return ReadingRepository.get_summary()