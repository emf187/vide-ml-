"""
Batched inference service.

Per product decision: ML inference does NOT run inline on every reading
(that would add latency to the ingest path). Instead it runs every
`batch_interval` readings per machine, or immediately if the rule engine
just flagged a critical event (so diagnosis stays timely when it matters
most). A per-machine counter is all the "batching" state that's needed —
no separate queue/worker process required at this scale.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from config.settings import get_settings
from fault_detection.engine import DetectionResult
from ml.explainer import explain_prediction
from ml.features import build_feature_vector
from ml.registry import ModelRegistry
from ml.schema import MLPrediction
from storage.repositories import MachineRepository, MLPredictionRepository

logger = logging.getLogger(__name__)


class BatchInferenceService:
    def __init__(self) -> None:
        self._cfg = get_settings()
        self._registry = ModelRegistry(self._cfg.ml.model_dir)
        self._model = self._registry.load_current()
        self._counters: dict[str, int] = {}

    def _should_run(self, machine_name: str, force: bool) -> bool:
        if force:
            return True
        interval = max(1, self._cfg.ml.batch_interval_readings)
        self._counters[machine_name] = self._counters.get(machine_name, 0) + 1
        return self._counters[machine_name] % interval == 0

    def maybe_predict(
        self, reading: dict, detection: DetectionResult, machine_name: str,
    ) -> Optional[MLPrediction]:
        """
        Called after every reading is processed. Runs inference only when
        the batch interval is hit, or the rule engine just flagged
        urgency='critical' (force=True in that case).
        """
        if not self._cfg.ml.enabled or self._model is None:
            return None

        force = detection.primary_fault is not None and detection.primary_fault.urgency == "critical"
        if not self._should_run(machine_name, force):
            return None

        machine = MachineRepository.get(machine_name)
        machine_type = machine["machine_type"] if machine else "custom"

        x = build_feature_vector(reading, detection, machine_type)
        raw = self._model.predict_one(x)
        explanation = explain_prediction(self._model.fault_type_model, x)

        prediction = MLPrediction(
            machine_name=machine_name,
            hours_to_maintenance=raw["hours_to_maintenance"],
            anomaly_probability_next_window=raw["anomaly_probability_next_window"],
            fault_type=raw["fault_type"],
            fault_type_confidence=raw["fault_type_confidence"],
            model_version=self._model.version,
            feature_importance=explanation["feature_importance"],
            top_factors=explanation["top_factors"],
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        try:
            MLPredictionRepository.insert(prediction)
        except Exception:
            logger.exception("Failed to store ML prediction for %s", machine_name)

        return prediction

    def reload_model(self) -> bool:
        """Called by ml/update_client.py after a new version is activated."""
        model = self._registry.load_current()
        if model is None:
            return False
        self._model = model
        logger.info("Inference service reloaded model version %s", model.version)
        return True


_singleton: Optional[BatchInferenceService] = None


def get_inference_service() -> BatchInferenceService:
    """
    Process-wide singleton — loading the XGBoost boosters from disk is
    too expensive to repeat per-reading. ml/update_client.py calls
    reload_model() on this same instance after activating a new version.
    """
    global _singleton
    if _singleton is None:
        _singleton = BatchInferenceService()
    return _singleton
