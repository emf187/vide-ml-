"""
Reading processing service.

Orchestrates the full pipeline for each incoming reading:
  1. Validate and normalize
  2. Register machine if new
  3. Run fault detection
  4. Compute energy/cost
  5. Store in database
  6. Return complete result
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from config.settings import get_settings
from fault_detection.engine import FaultDetectionEngine
from fusion.fusion_service import fuse
from ml.inference_service import get_inference_service
from ml.labels import flag_for_review
from storage.repositories import MachineRepository, ReadingRepository
from services.cloud_sync import forward_readings

logger = logging.getLogger(__name__)


class ReadingService:
    """Processes incoming sensor readings through the full pipeline."""

    def __init__(self) -> None:
        self._engine = FaultDetectionEngine()
        self._cfg = get_settings()

    def process(self, reading: dict) -> dict:
        """
        Process a single reading through validation → detection → storage.

        Args:
            reading: Raw reading dict from any data source. Must contain
                     at least 'machine_name'. Electrical fields are optional.

        Returns:
            Complete result dict with detection output, derived metrics,
            maintenance prediction, and storage status.
        """
        # 1. Validate
        machine_name = reading.get("machine_name", "").strip()
        if not machine_name:
            logger.warning("Dropping reading without machine_name")
            return {"error": "missing_machine_name", "status": "rejected"}

        # 2. Ensure machine is registered
        self._ensure_machine(reading)

        # 3. Run fault detection (raw object for fusion/ML, dict for storage/response)
        detection_result = self._engine.detect(reading)
        detection = self._engine.detect_serializable(reading)

        # 4. Compute derived fields
        derived = detection["derived_metrics"]
        power_kw = reading.get("power_kw", 0) or 0
        is_running = 1 if power_kw > 0.1 else 0
        load_factor_pct = derived.get("load_factor_pct", 0)

        # Energy accumulation (assume each reading represents a sampling interval)
        # Default: 5-second interval → 5/3600 hours
        sampling_hours = 5.0 / 3600.0
        energy_kwh = round(power_kw * sampling_hours, 6)

        # 5. Build storage record
        record = {
            "machine_name": machine_name,
            "source_type": reading.get("source_type", "unknown"),
            "voltage_v": reading.get("voltage_v"),
            "current_amps": reading.get("current_amps"),
            "power_kw": power_kw,
            "power_factor": reading.get("power_factor", 1.0),
            "frequency_hz": reading.get("frequency_hz", 50.0),
            "apparent_power_kva": derived.get("apparent_power_kva", 0),
            "reactive_power_kvar": derived.get("reactive_power_kvar", 0),
            "energy_kwh": energy_kwh,
            "temperature_c": reading.get("temperature_c"),
            "vibration_mm_s": reading.get("vibration_mm_s"),
            "acceleration_g": reading.get("acceleration_g"),
            "pressure_bar": reading.get("pressure_bar"),
            "flow_lpm": reading.get("flow_lpm"),
            "load_factor_pct": load_factor_pct,
            "is_running": is_running,
            "is_anomaly": 1 if detection["is_anomaly"] else 0,
            "anomaly_types": [f["type"] for f in detection["all_faults"]],
            "diagnosis": detection["primary_fault"],
            "timestamp": datetime.now().isoformat(),
        }

        # 6. Store
        try:
            row_id = ReadingRepository.insert(record)
        except Exception:
            logger.exception("Failed to store reading for %s", machine_name)
            return {"error": "storage_failure", "status": "error", "machine": machine_name}

        # 7. Maintenance prediction
        from services.maintenance_service import MaintenanceService
        maint_svc = MaintenanceService()
        maint_pred = maint_svc.predict(machine_name)

        # 7b. Flag for engineer review if the rule engine hit urgency='critical'.
        #     No automatic "failure" labeling — engineers confirm/dismiss via API.
        flag_for_review(machine_name, row_id, detection_result)

        # 7c. Batched ML inference — only actually runs every N readings,
        #     or immediately if this reading is critical. Returns None most calls.
        ml_prediction = None
        try:
            ml_prediction = get_inference_service().maybe_predict(reading, detection_result, machine_name)
        except Exception:
            logger.exception("ML inference failed for %s — continuing on rules only", machine_name)

        # 7d. Fire-and-forge forward reading to cloud endpoint — does NOT block
        #     the local response even if the cloud endpoint is unreachable.
        forward_readings(reading)

        # 7d. Fuse rule + ML output into the one object the chatbot/API consume
        fused = fuse(detection_result, ml_prediction, machine_name)

        # 8. Build response
        primary = detection.get("primary_fault")
        return {
            "status": "ok",
            "row_id": row_id,
            "machine": machine_name,
            "power_kw": power_kw,
            "is_anomaly": detection["is_anomaly"],
            "status_label": (
                f"ANOMALY: {primary['type']}" if primary else "Normal"
            ),
            "diagnosis": primary,
            "all_faults": detection["all_faults"],
            "derived_metrics": derived,
            "detection_context": detection.get("context_summary"),
            "maintenance_prediction": maint_pred,
            "ml_prediction": ml_prediction.__dict__ if ml_prediction else None,
            "fused_diagnosis": {
                "overall_urgency": fused.overall_urgency,
                "agreement": fused.agreement,
                "summary": fused.summary,
            },
            "energy_kwh": energy_kwh,
            "timestamp": record["timestamp"],
        }

    def _ensure_machine(self, reading: dict) -> None:
        """Register machine if it doesn't exist yet."""
        machine_name = reading["machine_name"]
        existing = MachineRepository.get(machine_name)
        if not existing:
            # Try to determine type from reading or default to 'custom'
            machine_type = reading.get("machine_type", "custom")
            location = reading.get("location", "unknown")
            rated = reading.get("rated_load_kw", 0)
            if not rated:
                rated = self._cfg.get_default_rated_load(machine_type)
            MachineRepository.register(
                machine_name=machine_name,
                machine_type=machine_type,
                location=location,
                rated_load_kw=rated,
            )
            logger.info("Auto-registered machine: %s (%s)", machine_name, machine_type)