"""
Modal retrain pipeline — DORMANT TEMPLATE, not deployed yet.

This is what training/bootstrap_pretrain.py graduates into once (a) your
Modal credits are back and (b) gateways have uploaded enough real data
to be worth retraining on. Until then, config.ml.cloud_sync.enabled
stays False and ml/update_client.py never calls this.

Deploying this later is: `pip install modal`, `modal deploy
training/retrain_pipeline.py`, then flip cloud_sync.enabled to true and
set endpoint_url to the deployed app's URL.

Expects gateways to have uploaded a batch of (reading, rule evaluations,
confirmed critical events) to a shared volume/bucket — that upload path
is a separate, still-to-build piece (a small endpoint on this same Modal
app that gateways POST to on a schedule). Left as a clear TODO rather
than guessed at, since the upload format should be designed together
once real field data exists to look at.
"""

from __future__ import annotations

# import modal  # uncomment once `pip install modal` is done for this deploy

# app = modal.App("vide-retrain")
# volume = modal.Volume.from_name("vide-model-registry", create_if_missing=True)
# image = modal.Image.debian_slim().pip_install("xgboost", "numpy")
#
#
# @app.function(image=image, volumes={"/models": volume}, schedule=modal.Cron("0 3 * * 0"))
# def retrain():
#     """Weekly retrain. TODO: replace synthetic bootstrap data with the
#     uploaded field dataset once the gateway upload endpoint exists."""
#     from training.synthetic_data import generate_dataset
#     from training.feature_pipeline import samples_to_training_data
#     from training.export_utils import export_for_deployment
#     from ml.model import VideMLModel
#
#     samples = generate_dataset(n_per_machine_type=400)  # TODO: + real uploaded data
#     data = samples_to_training_data(samples)
#     model = VideMLModel()
#     model.fit(data, version=None)
#     export_for_deployment(model, out_dir="/models", version=None)
#     volume.commit()
#
#
# @app.function(image=image, volumes={"/models": volume})
# @modal.fastapi_endpoint(method="GET")
# def latest_version():
#     """Matches what ml/update_client.py polls: {"version": "..."}."""
#     import os
#     versions = sorted(d for d in os.listdir("/models") if os.path.isdir(f"/models/{d}"))
#     return {"version": versions[-1] if versions else None}
#
#
# @app.function(image=image, volumes={"/models": volume})
# @modal.fastapi_endpoint(method="GET")
# def models(version: str, filename: str):
#     """Matches what ml/update_client.py downloads: raw model artifact files."""
#     from fastapi.responses import FileResponse
#     return FileResponse(f"/models/{version}/{filename}")

raise NotImplementedError(
    "retrain_pipeline.py is a dormant template — see the module docstring. "
    "Uncomment and adapt once Modal credits are available and the gateway "
    "data-upload endpoint is designed."
)
