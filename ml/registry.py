"""
Local model registry.

Models live under {model_dir}/{version}/ (e.g. models/v3/). A "current"
symlink-style pointer file tracks the active version. Swapping models is
atomic: write new version to disk fully, THEN update the pointer — a
crash mid-download never leaves the gateway pointing at a half-written
model.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

from ml.model import VideMLModel

logger = logging.getLogger(__name__)

POINTER_FILE = "current_version.json"


class ModelRegistry:
    def __init__(self, base_dir: str) -> None:
        self.base_dir = base_dir
        os.makedirs(base_dir, exist_ok=True)

    def _pointer_path(self) -> str:
        return os.path.join(self.base_dir, POINTER_FILE)

    def current_version(self) -> Optional[str]:
        path = self._pointer_path()
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return json.load(f).get("version")

    def version_dir(self, version: str) -> str:
        return os.path.join(self.base_dir, version)

    def save_new_version(self, model: VideMLModel, version: str, metrics: Optional[dict] = None) -> None:
        """Write a fully trained model to disk under its own version dir."""
        vdir = self.version_dir(version)
        model.save(vdir)
        with open(os.path.join(vdir, "metrics.json"), "w") as f:
            json.dump(metrics or {}, f)
        logger.info("Saved model version %s to %s", version, vdir)

    def activate_version(self, version: str) -> None:
        """Atomically point 'current' at a version that's already on disk."""
        vdir = self.version_dir(version)
        if not os.path.isdir(vdir):
            raise FileNotFoundError(f"Model version {version} not found at {vdir}")

        tmp_path = self._pointer_path() + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump({"version": version, "activated_at": time.time()}, f)
        os.replace(tmp_path, self._pointer_path())  # atomic on POSIX and Windows
        logger.info("Activated model version %s", version)

    def load_current(self) -> Optional[VideMLModel]:
        version = self.current_version()
        if version is None:
            return None
        try:
            return VideMLModel.load(self.version_dir(version))
        except Exception:
            logger.exception("Failed to load model version %s, rolling back", version)
            return self._rollback()

    def _rollback(self) -> Optional[VideMLModel]:
        """If the current version fails to load, fall back to the next-newest on disk."""
        versions = sorted(
            (d for d in os.listdir(self.base_dir) if os.path.isdir(self.version_dir(d))),
            reverse=True,
        )
        for v in versions:
            try:
                model = VideMLModel.load(self.version_dir(v))
                logger.warning("Rolled back to model version %s", v)
                self.activate_version(v)
                return model
            except Exception:
                continue
        logger.error("No loadable model version found — ML layer will stay disabled")
        return None
