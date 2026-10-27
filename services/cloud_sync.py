"""
Cloud sync forwarder - fire-and-forget forwarding of readings to cloud endpoint.

Sends readings to the configured cloud instance URL. Designed to never block
the local response; if the cloud endpoint is unreachable, the error is caught
and logged, and processing continues locally.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

import httpx

from config.settings import get_settings

logger = logging.getLogger(__name__)


def _forward_reading(reading: dict) -> None:
    """Send a single reading to the cloud endpoint in a thread-safe manner."""
    cfg = get_settings()
    endpoint = cfg.ml.cloud_sync.endpoint_url
    if not endpoint:
        logger.debug("Cloud sync endpoint not configured; skipping forward")
        return

    try:
        with httpx.Client(timeout=5.0) as client:
            client.post(
                endpoint,
                json={"reading": reading},
                headers={"Content-Type": "application/json"},
            )
    except Exception as e:
        logger.warning("Cloud sync forward failed: %s", e)


def forward_readings(reading: dict) -> None:
    """Fire-and-forge forward a reading to the cloud endpoint.

    This function returns immediately; the HTTP send happens in a daemon thread
    so the caller is never blocked, even if the cloud endpoint is unreachable.
    """
    thread = threading.Thread(target=_forward_reading, args=(reading,), daemon=True)
    thread.start()