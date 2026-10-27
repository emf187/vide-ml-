"""
Pulls new model versions from the cloud training pipeline and activates
them locally.

DORMANT FOR NOW: we're bootstrapping the first model manually from Colab
(see training/bootstrap_pretrain.py) rather than through this client —
there's no automated cloud endpoint to poll yet. This module is wired up
and ready; it just isn't called from anywhere until config.ml.cloud_sync
.enabled is turned on, which happens when the Modal retrain pipeline
(training/retrain_pipeline.py) is deployed.
"""

from __future__ import annotations

import logging

import httpx

from config.settings import get_settings
from ml.model import VideMLModel
from ml.registry import ModelRegistry

logger = logging.getLogger(__name__)


class ModelUpdateClient:
    def __init__(self) -> None:
        self._cfg = get_settings()
        self._registry = ModelRegistry(self._cfg.ml.model_dir)

    def check_and_update(self) -> bool:
        """
        Returns True if a new model version was downloaded and activated.
        Safe to call on a schedule (e.g. daily) — no-op if cloud_sync is
        disabled or nothing new is available.
        """
        sync_cfg = self._cfg.ml.cloud_sync
        if not sync_cfg.enabled:
            return False

        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.get(f"{sync_cfg.endpoint_url}/latest-version")
                resp.raise_for_status()
                latest = resp.json()["version"]

            if latest == self._registry.current_version():
                return False

            logger.info("New model version available: %s — downloading", latest)
            vdir = self._registry.version_dir(latest)
            self._download_version(sync_cfg.endpoint_url, latest, vdir)
            self._registry.activate_version(latest)
            return True

        except Exception:
            logger.exception("Model update check failed — staying on current version")
            return False

    def _download_version(self, endpoint_url: str, version: str, dest_dir: str) -> None:
        import os
        os.makedirs(dest_dir, exist_ok=True)
        with httpx.Client(timeout=120.0) as client:
            for filename in ("maintenance.json", "anomaly.json", "fault_type.json", "version.txt", "metrics.json"):
                resp = client.get(f"{endpoint_url}/models/{version}/{filename}")
                resp.raise_for_status()
                with open(os.path.join(dest_dir, filename), "wb") as f:
                    f.write(resp.content)
