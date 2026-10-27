"""
Abstract base class for all data sources.

Every data source (MQTT, HTTP, Serial) implements this interface,
allowing the AcquisitionManager to treat them uniformly.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Type alias for the callback that receives parsed readings
ReadingCallback = Callable[[dict], None]


class BaseSource(ABC):
    """
    Abstract data source.

    Lifecycle:
        start()  → begins receiving data
        stop()   → graceful shutdown
        is_running → bool indicating active state
    """

    def __init__(self, name: str, on_reading: Optional[ReadingCallback] = None) -> None:
        self.name = name
        self._on_reading = on_reading
        self._running = False
        self.logger = logging.getLogger(f"{__name__}.{name}")

    @property
    def is_running(self) -> bool:
        return self._running

    def set_callback(self, cb: ReadingCallback) -> None:
        self._on_reading = cb

    def _emit(self, reading: dict) -> None:
        """Safely invoke the reading callback with error handling."""
        if self._on_reading is None:
            self.logger.warning("No callback set for source %s, dropping reading", self.name)
            return
        try:
            self._on_reading(reading)
        except Exception:
            self.logger.exception("Callback error in source %s", self.name)

    @abstractmethod
    def start(self) -> None:
        """Start receiving data. Non-blocking."""
        ...

    @abstractmethod
    def stop(self) -> None:
        """Stop receiving data and release resources."""
        ...


class BaseParser(ABC):
    """
    Abstract parser to convert raw payload bytes/strings into
    normalized reading dicts.
    """

    @abstractmethod
    def parse(self, raw_payload: Any) -> Optional[dict]:
        """
        Parse raw payload into a normalized reading dict.

        Expected output keys (all optional — source determines what's available):
            machine_name, source_type,
            voltage_v, current_amps, power_kw, power_factor, frequency_hz,
            energy_kwh, temperature_c, vibration_mm_s, acceleration_g,
            pressure_bar, flow_lpm,
            rated_load_kw, nominal_voltage
        """
        ...