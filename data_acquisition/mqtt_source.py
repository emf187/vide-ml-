"""
MQTT data source for ESP32 and SIM-enabled boards.

Features:
  - Configurable topic patterns with factory ID substitution
  - Exponential backoff reconnection
  - QoS 1 delivery guarantee
  - JSON and plain-text payload parsing
  - Connection state tracking
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Optional

import paho.mqtt.client as mqtt

from config.settings import get_settings
from .base import BaseSource, BaseParser, ReadingCallback

logger = logging.getLogger(__name__)


class JsonPayloadParser(BaseParser):
    """Parse JSON payloads from ESP32 devices."""

    # Common field name mappings (ESP32 firmware may use different names)
    FIELD_MAP = {
        "v": "voltage_v",
        "voltage": "voltage_v",
        "voltage_v": "voltage_v",
        "i": "current_amps",
        "current": "current_amps",
        "current_amps": "current_amps",
        "p": "power_kw",
        "power": "power_kw",
        "power_kw": "power_kw",
        "pf": "power_factor",
        "powerfactor": "power_factor",
        "power_factor": "power_factor",
        "f": "frequency_hz",
        "freq": "frequency_hz",
        "frequency": "frequency_hz",
        "frequency_hz": "frequency_hz",
        "e": "energy_kwh",
        "energy": "energy_kwh",
        "energy_kwh": "energy_kwh",
        "temp": "temperature_c",
        "temperature": "temperature_c",
        "temperature_c": "temperature_c",
        "vib": "vibration_mm_s",
        "vibration": "vibration_mm_s",
        "pressure": "pressure_bar",
        "flow": "flow_lpm",
    }

    def parse(self, raw_payload: Any) -> Optional[dict]:
        if isinstance(raw_payload, (bytes, bytearray)):
            raw_payload = raw_payload.decode("utf-8", errors="replace")

        if isinstance(raw_payload, str):
            try:
                data = json.loads(raw_payload)
            except json.JSONDecodeError:
                # Try comma-separated format: "machine_name,voltage,current,power,pf,freq"
                return self._parse_csv(raw_payload)
        elif isinstance(raw_payload, dict):
            data = raw_payload
        else:
            return None

        if not isinstance(data, dict):
            return None

        # Normalize field names
        reading: dict[str, Any] = {"source_type": "mqtt"}
        for raw_key, std_key in self.FIELD_MAP.items():
            if raw_key in data:
                val = data[raw_key]
                try:
                    reading[std_key] = float(val)
                except (TypeError, ValueError):
                    pass

        # Machine name: try common keys
        for mk in ("machine_name", "machine", "device", "id", "name", "device_id"):
            if mk in data and isinstance(data[mk], str) and data[mk].strip():
                reading["machine_name"] = data[mk].strip()
                break

        # Rated load
        for rk in ("rated_load_kw", "rated_kw", "rated_power", "rated"):
            if rk in data:
                try:
                    reading["rated_load_kw"] = float(data[rk])
                except (TypeError, ValueError):
                    pass
                break

        # Pass through any unrecognized fields as extras
        known_keys = set(self.FIELD_MAP.values()) | {"source_type", "machine_name", "rated_load_kw", "nominal_voltage"}
        for k, v in data.items():
            if k not in known_keys and k not in self.FIELD_MAP:
                reading[f"extra_{k}"] = v

        if "machine_name" not in reading:
            return None

        return reading

    def _parse_csv(self, text: str) -> Optional[dict]:
        """Parse comma-separated: machine_name,voltage,current,power,pf,freq[,rated_kw]"""
        parts = text.strip().split(",")
        if len(parts) < 6:
            return None
        try:
            reading = {
                "source_type": "mqtt",
                "machine_name": parts[0].strip(),
                "voltage_v": float(parts[1]),
                "current_amps": float(parts[2]),
                "power_kw": float(parts[3]),
                "power_factor": float(parts[4]),
                "frequency_hz": float(parts[5]),
            }
            if len(parts) > 6:
                reading["rated_load_kw"] = float(parts[6])
            return reading
        except (ValueError, IndexError):
            return None


class MQTTSource(BaseSource):
    """
    MQTT data source with automatic reconnection.

    Subscribes to configured topic patterns and parses incoming
    messages using the provided parser.
    """

    def __init__(
        self,
        parser: Optional[BaseParser] = None,
        on_reading: Optional[ReadingCallback] = None,
    ) -> None:
        super().__init__("mqtt", on_reading)
        self._parser = parser or JsonPayloadParser()
        self._client: Optional[mqtt.Client] = None
        self._connect_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._stats = {"received": 0, "parsed": 0, "errors": 0, "reconnects": 0}

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    def _get_config(self):
        return get_settings().communication.mqtt

    def _resolve_topic(self, pattern: str) -> str:
        factory_id = get_settings().factory.get("id", "factory-001")
        return pattern.replace("{factory_id}", factory_id)

    def _on_connect(self, client: mqtt.Client, flags: dict, reason_code: mqtt.ReasonCode, properties: Any = None) -> None:
        if reason_code == 0:
            self.logger.info("Connected to MQTT broker")
            cfg = self._get_config()
            topic = self._resolve_topic(cfg.topics.get("subscribe", ""))
            client.subscribe(topic, qos=cfg.qos)
            self.logger.info("Subscribed to: %s", topic)
        else:
            self.logger.warning("MQTT connect failed: %s", reason_code)

    def _on_disconnect(self, client: mqtt.Client, reason_code: mqtt.ReasonCode, properties: Any = None) -> None:
        self.logger.warning("MQTT disconnected: %s", reason_code)
        if not self._stop_event.is_set():
            self._stats["reconnects"] += 1

    def _on_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        self._stats["received"] += 1
        try:
            topic = msg.topic
            reading = self._parser.parse(msg.payload)
            if reading:
                reading["mqtt_topic"] = topic
                # Extract machine name from topic if not in payload
                if "machine_name" not in reading:
                    parts = topic.split("/")
                    for p in reversed(parts):
                        if p and p != "data" and not p.startswith("{"):
                            reading["machine_name"] = p
                            break
                self._stats["parsed"] += 1
                self._emit(reading)
            else:
                self._stats["errors"] += 1
                self.logger.debug("Failed to parse message on %s (%d bytes)", topic, len(msg.payload))
        except Exception:
            self._stats["errors"] += 1
            self.logger.exception("Error processing MQTT message")

    def _connect_loop(self) -> None:
        """Background thread: maintain MQTT connection with exponential backoff."""
        cfg = self._get_config()
        delay = cfg.reconnect.initial_delay_ms / 1000.0

        while not self._stop_event.is_set():
            try:
                self._client = mqtt.Client(
                    client_id=cfg.client_id,
                    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                )
                if cfg.username and cfg.password:
                    self._client.username_pw_set(cfg.username, cfg.password)
                self._client.on_connect = self._on_connect
                self._client.on_disconnect = self._on_disconnect
                self._client.on_message = self._on_message

                self._client.connect(cfg.broker, cfg.port, keepalive=cfg.keepalive_seconds)
                self._running = True
                self._client.loop_forever()
            except Exception:
                self.logger.exception("MQTT connection error, retrying in %.1fs", delay)
            finally:
                self._running = False
                if self._client:
                    try:
                        self._client.disconnect()
                    except Exception:
                        pass
                    self._client = None

            # Exponential backoff
            self._stop_event.wait(delay)
            delay = min(delay * cfg.reconnect.backoff_multiplier, cfg.reconnect.max_delay_ms / 1000.0)

            if cfg.reconnect.max_retries is not None and self._stats["reconnects"] >= cfg.reconnect.max_retries:
                self.logger.error("MQTT max reconnect attempts reached, stopping")
                break

    def start(self) -> None:
        if self._running:
            return
        cfg = self._get_config()
        if not cfg.enabled:
            self.logger.info("MQTT source disabled in configuration")
            return

        # Quick reachability check: avoid noisy reconnect loops if broker is unreachable.
        import socket
        try:
            with socket.create_connection((cfg.broker, cfg.port), timeout=1):
                pass
        except Exception:
            self.logger.warning(
                "MQTT broker %s:%d unreachable — MQTT source will remain disabled until available",
                cfg.broker, cfg.port,
            )
            return

        self._stop_event.clear()
        self._connect_thread = threading.Thread(
            target=self._connect_loop, name="mqtt-source", daemon=True
        )
        self._connect_thread.start()
        self.logger.info("MQTT source started (broker: %s:%d)", cfg.broker, cfg.port)

    def stop(self) -> None:
        self._stop_event.set()
        if self._client:
            try:
                self._client.disconnect()
            except Exception:
                pass
        if self._connect_thread and self._connect_thread.is_alive():
            self._connect_thread.join(timeout=5.0)
        self._running = False
        self.logger.info("MQTT source stopped. Stats: %s", self._stats)