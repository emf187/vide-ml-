"""Anomaly query endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from storage.repositories import ReadingRepository

router = APIRouter(prefix="/api/anomalies", tags=["anomalies"])


@router.get("")
def list_anomalies(limit: int = 20):
    return ReadingRepository.get_anomalies(limit=limit)


@router.get("/machine/{machine_name}")
def machine_anomalies(machine_name: str, limit: int = 20):
    return ReadingRepository.get_anomalies_for_machine(machine_name, limit=limit)