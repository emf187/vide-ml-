"""
Acquisition manager — orchestrates all data sources.

Receives readings from MQTT, HTTP, and Serial sources,
validates them, and dispatches to the reading service for
fault detection and storage.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from .base import ReadingCallback
from .mqtt_source import MQTTSource
from .http_source import HTTPPollerSource

logger = logging.getLogger(__name__)


class AcquisitionManager:
    """
    Central manager for all data acquisition sources.

    Starts/stops all configured sources and routes readings
    to the reading service for processing.
    """

    def __init__(self, on_reading: Optional[ReadingCallback] = None) -> None:
        self._on_reading = on_reading
        self._mqtt = MQTTSource(on_reading=self._on_reading_received)
        self._http = HTTPPollerSource(on_reading=self._on_reading_received)
        self._started = False
        self._start_time: Optional[float] = None

    def _on_reading_received(self, reading: dict) -> None:
        """Internal callback — adds metadata before forwarding."""
        reading["received_at"] = time.time()
        if self._on_reading:
            self._on_reading(reading)

    def start(self) -> None:
        """Start all configured data sources."""
        if self._started:
            logger.warning("AcquisitionManager already started")
            return

        self._start_time = time.time()
        self._mqtt.start()
        self._http.start()
        # Serial source can be added here when needed
        self._started = True
        logger.info("AcquisitionManager started — all sources initialized")

    def stop(self) -> None:
        """Stop all data sources gracefully."""
        self._mqtt.stop()
        self._http.stop()
        self._started = False
        uptime = time.time() - self._start_time if self._start_time else 0
        logger.info("AcquisitionManager stopped (uptime: %.0fs)", uptime)

    @property
    def is_running(self) -> bool:
        return self._started

    def get_status(self) -> dict:
        """Return status of all sources for monitoring."""
        return {
            "running": self._started,
            "uptime_seconds": round(time.time() - self._start_time, 1) if self._start_time else 0,
            "sources": {
                "mqtt": {
                    "running": self._mqtt.is_running,
                    "stats": self._mqtt.stats,
                },
                "http": {
                    "running": self._http.is_running,
                    "stats": self._http.stats,
                },
            },
        }