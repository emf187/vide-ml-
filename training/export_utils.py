"""
Packages a trained VideMLModel into the exact directory layout
ml/registry.py expects, then zips it for easy download out of Colab.

On the gateway side, deploying an exported model is just:
    unzip vide_model_v1.zip -d models/
    # then activate it:
    ModelRegistry("models").activate_version("v1")
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone

from ml.model import VideMLModel


def make_version_string() -> str:
    return "v" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def export_for_deployment(model: VideMLModel, out_dir: str, version: str | None = None, metrics: dict | None = None) -> str:
    """
    Saves the model to {out_dir}/{version}/ and zips it to
    {out_dir}/{version}.zip. Returns the path to the zip file — that's
    the one file to download from Colab.
    """
    import json
    import os

    version = version or make_version_string()
    model.version = version
    vdir = os.path.join(out_dir, version)
    model.save(vdir)

    if metrics:
        with open(os.path.join(vdir, "metrics.json"), "w") as f:
            json.dump(metrics, f, indent=2)

    zip_base = os.path.join(out_dir, version)
    zip_path = shutil.make_archive(zip_base, "zip", root_dir=out_dir, base_dir=version)
    return zip_path
