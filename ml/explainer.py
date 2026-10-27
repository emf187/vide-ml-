"""
Explainability via XGBoost's built-in TreeSHAP (pred_contribs=True).

Why this instead of the `shap` package: XGBoost has exact TreeSHAP
implemented in C++ internally — calling predict(pred_contribs=True) gives
the same values the `shap` package would compute for a tree model, with
no extra dependency, no Python-level tree walking, and sub-millisecond
overhead per prediction. This is what keeps explainability inside the
<50ms budget on edge hardware.
"""

from __future__ import annotations

import numpy as np
import xgboost as xgb

from ml.schema import FEATURE_ORDER


def explain_prediction(model: xgb.XGBRegressor | xgb.XGBClassifier, x: np.ndarray, top_n: int = 5) -> dict:
    """
    Returns SHAP-style contributions for one sample against one booster
    (typically the fault_type or anomaly model — the one driving the
    diagnosis the user is asking about).

    Multiclass boosters (fault_type_model) return pred_contribs shaped
    (1, n_classes, n_features+1) — one contribution vector per class. We
    explain the class the model actually predicted, not class 0.
    """
    booster = model.get_booster()
    x2d = x.reshape(1, -1)
    dmat = xgb.DMatrix(x2d, feature_names=FEATURE_ORDER)
    contribs = booster.predict(dmat, pred_contribs=True)[0]

    if contribs.ndim == 2:
        # multiclass: (n_classes, n_features+1) — pick the predicted class
        predicted_class = int(model.predict(x2d)[0])
        contribs = contribs[predicted_class]

    feature_contribs = dict(zip(FEATURE_ORDER, contribs[:-1]))
    ranked = sorted(feature_contribs.items(), key=lambda kv: abs(kv[1]), reverse=True)

    return {
        "feature_importance": {k: round(float(v), 4) for k, v in ranked[:top_n]},
        "top_factors": [_describe_factor(k, v) for k, v in ranked[:top_n]],
        "bias": round(float(contribs[-1]), 4),
    }


def _describe_factor(feature_name: str, contribution: float) -> str:
    direction = "increased" if contribution > 0 else "decreased"
    return f"{feature_name} {direction} risk (contribution {contribution:+.3f})"
