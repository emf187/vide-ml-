"""
HTTP polling data source.

Periodically fetches readings from ESP32 HTTP endpoints.
Supports multiple devices with independent intervals and timeouts.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

import httpx

from config.settings import get_settings
from .base import BaseSource, BaseParser, ReadingCallback
from .mqtt_source import JsonPayloadParser

logger = logging.getLogger(__name__)


class HTTPPollerSource(BaseSource):
    """
    Polls one or more HTTP endpoints at configured intervals.

    Each poller runs in its own daemon thread for isolation.
    """

    def __init__(
        self,
        parser: Optional[BaseParser] = None,
        on_reading: Optional[ReadingCallback] = None,
    ) -> None:
        super().__init__("http", on_reading)
        self._parser = parser or JsonPayloadParser()
        self._threads: list[threading.Thread] = []
        self._stop_event = threading.Event()
        self._stats = {"requests": 0, "success": 0, "errors": 0}

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    def _poll_device(self, poller_cfg: Any) -> None:
        """Poll a single device endpoint in a loop."""
        name = poller_cfg.name
        url = poller_cfg.url
        interval = poller_cfg.interval_seconds
        timeout = poller_cfg.timeout_seconds
        headers = poller_cfg.headers or {}

        logger.info("HTTP poller started: %s → %s (every %.1fs)", name, url, interval)

        while not self._stop_event.is_set():
            self._stats["requests"] += 1
            try:
                with httpx.Client(timeout=timeout) as client:
                    resp = client.get(url, headers=headers)
                    resp.raise_for_status()

                    # Try to parse as JSON
                    try:
                        data = resp.json()
                    except Exception:
                        data = resp.text

                    reading = self._parser.parse(data)
                    if reading:
                        reading["source_type"] = "http"
                        reading["http_endpoint"] = url
                        if "machine_name" not in reading:
                            reading["machine_name"] = name
                        self._stats["success"] += 1
                        self._emit(reading)
                    else:
                        self._stats["errors"] += 1
                        logger.debug("Failed to parse HTTP response from %s", url)

            except httpx.TimeoutException:
                self._stats["errors"] += 1
                logger.warning("HTTP poller %s: timeout fetching %s", name, url)
            except httpx.HTTPStatusError as e:
                self._stats["errors"] += 1
                logger.warning("HTTP poller %s: HTTP %d from %s", name, e.response.status_code, url)
            except Exception:
                self._stats["errors"] += 1
                logger.exception("HTTP poller %s: error fetching %s", name, url)

            # Wait for next interval (interruptible)
            self._stop_event.wait(interval)

        logger.info("HTTP poller stopped: %s", name)

    def start(self) -> None:
        if self._running:
            return
        cfg = get_settings().communication.http
        if not cfg.enabled or not cfg.pollers:
            self.logger.info("HTTP source disabled or no pollers configured")
            return

        self._stop_event.clear()
        for poller in cfg.pollers:
            t = threading.Thread(
                target=self._poll_device, args=(poller,),
                name=f"http-poller-{poller.name}", daemon=True,
            )
            t.start()
            self._threads.append(t)

        self._running = True
        self.logger.info("HTTP source started with %d poller(s)", len(cfg.pollers))

    def stop(self) -> None:
        self._stop_event.set()
        for t in self._threads:
            if t.is_alive():
                t.join(timeout=5.0)
        self._threads.clear()
        self._running = False
        self.logger.info("HTTP source stopped. Stats: %s", self._stats)