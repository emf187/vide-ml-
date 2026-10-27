"""Maintenance recording and prediction endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from storage.repositories import MaintenanceRepository, MachineRepository
from services.maintenance_service import MaintenanceService

router = APIRouter(prefix="/api/maintenance", tags=["maintenance"])


class MaintenanceInput(BaseModel):
    machine_name: str
    maintenance_type: str
    hours_at_maintenance: float = 0
    parts_replaced: str = "none"
    technician: str = "unknown"
    cost: float = 0
    next_due_hours: float = 2000
    notes: str = ""


@router.post("/record")
def record_maintenance(body: MaintenanceInput):
    machine = MachineRepository.get(body.machine_name)
    if not machine:
        raise HTTPException(status_code=404, detail=f"Machine '{body.machine_name}' not found")
    try:
        row_id = MaintenanceRepository.record(body.model_dump())
        return {"status": "ok", "id": row_id, "machine": body.machine_name}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/history/{machine_name}")
def get_history(machine_name: str):
    return MaintenanceRepository.get_history(machine_name)


@router.get("/prediction/{machine_name}")
def get_prediction(machine_name: str):
    machine = MachineRepository.get(machine_name)
    if not machine:
        raise HTTPException(status_code=404, detail=f"Machine '{machine_name}' not found")
    svc = MaintenanceService()
    return svc.predict(machine_name)


@router.get("/predictions")
def get_all_predictions():
    machines = MachineRepository.list_all()
    svc = MaintenanceService()
    results = []
    for m in machines:
        pred = svc.predict(m["machine_name"])
        results.append({**m, "prediction": pred})
    # Sort by urgency
    urgency_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "unknown": 4}
    results.sort(key=lambda x: urgency_order.get(x["prediction"]["urgency"], 5))
    return results