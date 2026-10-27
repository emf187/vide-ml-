"""
Bootstrap pretraining — run this one in Colab first.

HOW TO RUN IN COLAB
--------------------
1. Upload/clone the repo so this file sits at its normal path
   (vide/training/bootstrap_pretrain.py) — imports below rely on the
   package layout, not on being copy-pasted standalone.
2. New Colab notebook, first cell:
       !git clone <your repo url> vide
       %cd vide
       !pip install -q xgboost numpy
3. Second cell:
       %run training/bootstrap_pretrain.py
   Or paste this file's contents into cells split at the "# ---- cell"
   markers below.
4. It prints quick metrics, then downloads a zip — that zip is the
   entire model, ready to drop into models/ on the gateway node.

WHAT THIS PRODUCES
-------------------
A model trained on synthetic-but-physics-consistent data (see
training/synthetic_data.py — every fault scenario is built from the
same thresholds and rule logic as fault_detection/rules.py, so the
model starts out already aligned with how your rule engine thinks).
It is a BASELINE, not a finished product — it gets better as real field
data and engineer-confirmed critical events accumulate (see
ml/labels.py) and gets retrained, eventually on Modal instead of here.
"""

from __future__ import annotations

# ---- cell: generate data
from training.synthetic_data import generate_dataset
from training.feature_pipeline import samples_to_training_data

print("Generating synthetic bootstrap dataset...")
samples = generate_dataset(n_per_machine_type=400, fault_ratio=0.35, seed=42)
print(f"Generated {len(samples)} samples across machine types")

data = samples_to_training_data(samples)
print(f"Feature matrix: {data.X.shape}")

# ---- cell: train on the full bootstrap set
import numpy as np
from ml.model import VideMLModel

model = VideMLModel()
model.fit(data, version="bootstrap")

# ---- cell: evaluate on a FRESH synthetic batch (different seed) — a truer
# generalization check than a random split of the same correlated data,
# and sidesteps rare fault classes being absent from a small held-out fold.
eval_samples = generate_dataset(n_per_machine_type=100, fault_ratio=0.35, seed=999)
eval_data = samples_to_training_data(eval_samples)

pred_hours = model.maintenance_model.predict(eval_data.X)
mae_hours = float(np.mean(np.abs(pred_hours - eval_data.hours_to_maintenance)))

anomaly_pred = model.anomaly_model.predict(eval_data.X)
anomaly_acc = float(np.mean(anomaly_pred == eval_data.anomaly_next_window))

fault_pred = model.fault_type_model.predict(eval_data.X)
fault_acc = float(np.mean(fault_pred == eval_data.fault_type))

print(f"hours_to_maintenance MAE:        {mae_hours:.1f}h")
print(f"anomaly_next_window accuracy:    {anomaly_acc * 100:.1f}%")
print(f"fault_type accuracy:             {fault_acc * 100:.1f}%")
print("(Synthetic-data metrics — expect these to be optimistic. Real")
print(" numbers will look different once field data flows in.)")

# ---- cell: export + download
from training.export_utils import export_for_deployment

zip_path = export_for_deployment(
    model, out_dir="exported_models", version="v1_bootstrap",
    metrics={"mae_hours": mae_hours, "anomaly_acc": anomaly_acc, "fault_type_acc": fault_acc, "source": "synthetic_bootstrap"},
)
print(f"Exported: {zip_path}")

try:
    from google.colab import files  # noqa: F401
    files.download(zip_path)
    print("Download started — save this zip, you'll unzip it into models/ on the gateway.")
except ImportError:
    print("Not running in Colab — zip is at the path above, copy it to the gateway's models/ dir manually.")
