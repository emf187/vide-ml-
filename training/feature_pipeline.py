"""
Builds a TrainingData matrix from SyntheticSample records using the
SAME ml/features.py::build_feature_vector() function that live inference
calls. This is the guarantee that training and inference never drift:
there is exactly one feature-building function in the whole codebase.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from fault_detection.rules import compute_derived
from ml.features import build_feature_vector
from ml.model import TrainingData
from training.synthetic_data import SyntheticSample


@dataclass
class _DetectionAdapter:
    """Duck-types fault_detection.engine.DetectionResult's fields that
    build_feature_vector() actually reads — derived_metrics, context,
    all_rule_evaluations — without depending on a live rule-engine run."""
    derived_metrics: dict
    context: Any
    all_rule_evaluations: dict


def samples_to_training_data(samples: list[SyntheticSample]) -> TrainingData:
    rows = []
    hours, anomaly, fault_idx = [], [], []

    for s in samples:
        r = s.reading
        derived = compute_derived(r.get("power_kw", 0) or 0, r.get("voltage_v", 0) or 0,
                                   r.get("current_amps", 0) or 0, r.get("rated_load_kw", 10))
        adapter = _DetectionAdapter(derived_metrics=derived, context=s.context, all_rule_evaluations=s.all_rule_evaluations)

        rows.append(build_feature_vector(r, adapter, s.machine_type))
        hours.append(s.hours_to_maintenance)
        anomaly.append(s.anomaly_next_window)
        fault_idx.append(s.fault_type_idx)

    return TrainingData(
        X=np.vstack(rows),
        hours_to_maintenance=np.array(hours, dtype=np.float32),
        anomaly_next_window=np.array(anomaly, dtype=np.int32),
        fault_type=np.array(fault_idx, dtype=np.int32),
    )
