"""
XGBoost wrapper providing three prediction heads from one feature vector:

  1. hours_to_maintenance   — regression
  2. anomaly_next_window    — binary classification
  3. fault_type             — multiclass classification (9 rule types + "normal")

Three small boosters instead of one multi-output model — this keeps each
head independently retrainable/debuggable, and none of them individually
comes close to the <100MB / <50ms budget (typical: <2MB, <5ms each on a
Pi-class CPU with 50-100 trees, depth<=5).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import xgboost as xgb

from ml.schema import FAULT_TYPE_CLASSES, FEATURE_ORDER

logger = logging.getLogger(__name__)

DEFAULT_PARAMS = dict(
    n_estimators=80,
    max_depth=4,
    learning_rate=0.1,
    subsample=0.8,
    colsample_bytree=0.8,
    n_jobs=2,
    tree_method="hist",
)


@dataclass
class TrainingData:
    X: np.ndarray                      # shape (n, len(FEATURE_ORDER))
    hours_to_maintenance: np.ndarray    # shape (n,) float
    anomaly_next_window: np.ndarray     # shape (n,) 0/1
    fault_type: np.ndarray              # shape (n,) int, index into FAULT_TYPE_CLASSES


class VideMLModel:
    """Bundles the three boosters and handles fit / predict / save / load."""

    def __init__(self) -> None:
        self.maintenance_model: Optional[xgb.XGBRegressor] = None
        self.anomaly_model: Optional[xgb.XGBClassifier] = None
        self.fault_type_model: Optional[xgb.XGBClassifier] = None
        self.version: str = "untrained"

    def fit(self, data: TrainingData, version: str) -> None:
        logger.info("Training VideMLModel version=%s on %d samples", version, len(data.X))

        self.maintenance_model = xgb.XGBRegressor(**DEFAULT_PARAMS, objective="reg:squarederror")
        self.maintenance_model.fit(data.X, data.hours_to_maintenance)

        self.anomaly_model = xgb.XGBClassifier(**DEFAULT_PARAMS, objective="binary:logistic", eval_metric="logloss")
        self.anomaly_model.fit(data.X, data.anomaly_next_window)

        # num_class is intentionally NOT passed here — XGBoost infers it from
        # the unique values in y at fit time. Passing it explicitly causes a
        # hard error if any training fold happens to be missing a rare class.
        self.fault_type_model = xgb.XGBClassifier(**DEFAULT_PARAMS, objective="multi:softprob", eval_metric="mlogloss")
        self.fault_type_model.fit(data.X, data.fault_type)

        self.version = version

    def predict_one(self, x: np.ndarray) -> dict:
        """x: 1D array matching FEATURE_ORDER."""
        if self.maintenance_model is None:
            raise RuntimeError("Model not loaded/trained yet")

        x2d = x.reshape(1, -1)
        hours = float(self.maintenance_model.predict(x2d)[0])
        anomaly_prob = float(self.anomaly_model.predict_proba(x2d)[0][1])
        fault_probs = self.fault_type_model.predict_proba(x2d)[0]
        fault_idx = int(np.argmax(fault_probs))

        return {
            "hours_to_maintenance": max(0.0, hours),
            "anomaly_probability_next_window": anomaly_prob,
            "fault_type": FAULT_TYPE_CLASSES[fault_idx],
            "fault_type_confidence": float(fault_probs[fault_idx]),
        }

    def save(self, dir_path: str) -> None:
        import os
        os.makedirs(dir_path, exist_ok=True)
        self.maintenance_model.save_model(f"{dir_path}/maintenance.json")
        self.anomaly_model.save_model(f"{dir_path}/anomaly.json")
        self.fault_type_model.save_model(f"{dir_path}/fault_type.json")
        with open(f"{dir_path}/version.txt", "w") as f:
            f.write(self.version)

    @classmethod
    def load(cls, dir_path: str) -> "VideMLModel":
        m = cls()
        m.maintenance_model = xgb.XGBRegressor()
        m.maintenance_model.load_model(f"{dir_path}/maintenance.json")
        m.anomaly_model = xgb.XGBClassifier()
        m.anomaly_model.load_model(f"{dir_path}/anomaly.json")
        m.fault_type_model = xgb.XGBClassifier()
        m.fault_type_model.load_model(f"{dir_path}/fault_type.json")
        try:
            with open(f"{dir_path}/version.txt") as f:
                m.version = f.read().strip()
        except FileNotFoundError:
            m.version = "unknown"
        return m
