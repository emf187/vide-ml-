"""Machine registration and listing endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from storage.repositories import MachineRepository, ReadingRepository
from services.maintenance_service import MaintenanceService

router = APIRouter(prefix="/api/machines", tags=["machines"])


class MachineCreate(BaseModel):
    machine_name: str
    machine_type: str = "custom"
    location: str = "unknown"
    rated_load_kw: float = 10.0


@router.get("")
def list_machines():
    machines = MachineRepository.list_all()
    maint_svc = MaintenanceService()
    result = []
    for m in machines:
        stats = ReadingRepository.get_machine_stats(m["machine_name"])
        pred = maint_svc.predict(m["machine_name"])

        # Health status
        u = pred["urgency"]
        health = (
            "critical" if u == "critical" else
            "warning" if u == "high" else
            "watch" if u == "medium" else
            "healthy"
        )

        result.append({
            **m,
            "stats": stats,
            "maintenance_prediction": {
                "hours_remaining": pred["hours_remaining"],
                "urgency": pred["urgency"],
                "recommended_date": pred["recommended_maintenance_date"],
            },
            "health_status": health,
        })
    return result


@router.post("")
def create_machine(body: MachineCreate):
    try:
        MachineRepository.register(
            machine_name=body.machine_name,
            machine_type=body.machine_type,
            location=body.location,
            rated_load_kw=body.rated_load_kw,
        )
        return {"status": "ok", "machine": body.machine_name}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{machine_name}")
def get_machine(machine_name: str):
    machine = MachineRepository.get(machine_name)
    if not machine:
        raise HTTPException(status_code=404, detail=f"Machine '{machine_name}' not found")

    stats = ReadingRepository.get_machine_stats(machine_name)
    maint_svc = MaintenanceService()
    pred = maint_svc.predict(machine_name)
    history = ReadingRepository.get_machine_history(machine_name, limit=200)

    from storage.repositories import MaintenanceRepository
    maint_history = MaintenanceRepository.get_history(machine_name)

    return {
        **machine,
        "stats": stats,
        "maintenance_prediction": pred,
        "readings": history,
        "maintenance_history": maint_history,
    }


@router.delete("/{machine_name}")
def delete_machine(machine_name: str):
    deleted = MachineRepository.delete(machine_name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Machine '{machine_name}' not found")
    return {"status": "ok", "deleted": machine_name}