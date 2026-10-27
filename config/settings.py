"""
Configuration management module.

Loads settings from YAML file with environment variable overrides.
Supports hot-reload for threshold and rule changes at runtime.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field, field_validator


# ── Pydantic configuration models ────────────────────────────────────────────

class ReconnectConfig(BaseModel):
    initial_delay_ms: int = 1000
    max_delay_ms: int = 30000
    backoff_multiplier: float = 2.0
    max_retries: Optional[int] = None


class MQTTConfig(BaseModel):
    enabled: bool = False
    broker: str = "localhost"
    port: int = 1883
    username: Optional[str] = None
    password: Optional[str] = None
    client_id: str = "vide-industrial"
    topics: dict[str, str] = Field(default_factory=lambda: {"subscribe": "factory/{factory_id}/sensors/+/data"})
    qos: int = 1
    keepalive_seconds: int = 60
    reconnect: ReconnectConfig = Field(default_factory=ReconnectConfig)


class HTTPPollerConfig(BaseModel):
    name: str
    url: str
    interval_seconds: float = 5.0
    timeout_seconds: float = 3.0
    headers: dict[str, str] = Field(default_factory=dict)


class HTTPConfig(BaseModel):
    enabled: bool = False
    pollers: list[HTTPPollerConfig] = Field(default_factory=list)


class SerialPortConfig(BaseModel):
    port: str = "/dev/ttyUSB0"
    baud_rate: int = 115200
    data_bits: int = 8
    parity: str = "none"
    stop_bits: int = 1
    timeout_seconds: float = 1.0


class SerialConfig(BaseModel):
    enabled: bool = False
    ports: list[SerialPortConfig] = Field(default_factory=list)


class CommunicationConfig(BaseModel):
    mqtt: MQTTConfig = Field(default_factory=MQTTConfig)
    http: HTTPConfig = Field(default_factory=HTTPConfig)
    serial: SerialConfig = Field(default_factory=SerialConfig)


class SensorParameter(BaseModel):
    name: str
    unit: str = ""
    min: Optional[float] = None
    max: Optional[float] = None
    nominal: Optional[float] = None


class SensorTypeConfig(BaseModel):
    parameters: list[SensorParameter] = Field(default_factory=list)


class EquipmentCategory(BaseModel):
    name: str = "Custom"
    default_maintenance_interval_hours: float = 2000
    default_rated_load_kw: float = 10.0


class ThresholdConfig(BaseModel):
    voltage_deviation_pct: float = 10.0
    voltage_critical_pct: float = 15.0
    frequency_deviation_hz: float = 0.5
    frequency_critical_hz: float = 1.0
    frequency_rocof_hz_per_s: float = 0.2
    overload_pct: float = 15.0
    overload_critical_pct: float = 30.0
    underload_pct: float = 70.0
    power_factor_min: float = 0.75
    power_factor_min_load_pct: float = 40.0
    reactive_to_apparent_ratio: float = 0.6
    reactive_min_power_kw: float = 1.0
    trend_increase_pct: float = 10.0
    trend_min_stability_ratio: float = 0.1
    sensor_min_power_for_validation: float = 0.5
    sensor_min_voltage_for_power: float = 50.0


class FaultDetectionConfig(BaseModel):
    enabled: bool = True
    rolling_window_size: int = 10
    rules: dict[str, bool] = Field(default_factory=lambda: {
        "voltage_sag": True, "voltage_swell": True,
        "frequency_instability": True, "mechanical_overload": True,
        "low_power_factor": True, "excessive_reactive_power": True,
        "mechanical_degradation": True, "underload": True,
        "sensor_anomaly": True,
    })


class MaintenanceUrgencyConfig(BaseModel):
    critical_hours: float = 100
    high_hours: float = 500
    medium_hours: float = 1000


class MaintenanceConfig(BaseModel):
    anomaly_penalty_hours: float = 50
    overload_penalty_hours_per_pct: float = 10
    voltage_stress_penalty_per_point: float = 5
    max_thermal_penalty_hours: float = 500
    urgency: MaintenanceUrgencyConfig = Field(default_factory=MaintenanceUrgencyConfig)


class DatabaseConfig(BaseModel):
    type: str = "sqlite"
    path: str = "data/industrial.db"
    wal_mode: bool = True


class RetentionConfig(BaseModel):
    raw_readings: int = 365
    aggregated: int = 730


class StorageConfig(BaseModel):
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    retention_days: RetentionConfig = Field(default_factory=RetentionConfig)


class LLMContextConfig(BaseModel):
    max_recent_readings: int = 20
    max_recent_anomalies: int = 10
    include_maintenance_prediction: bool = True
    include_derived_metrics: bool = True
    include_machine_stats: bool = True


class LLMConfig(BaseModel):
    enabled: bool = False
    provider: str = "groq"
    api_key_env_var: str = "GROQ_API_KEY"
    api_url: str = "https://api.groq.com/openai/v1/chat/completions"
    model: str = "qwen/qwen3.6-27b"
    temperature: float = 0.7
    max_tokens: int = 2048
    timeout_seconds: float = 30.0
    context: LLMContextConfig = Field(default_factory=LLMContextConfig)


class CloudSyncConfig(BaseModel):
    enabled: bool = False  # dormant until the Modal retrain pipeline is deployed
    endpoint_url: str = ""
    check_interval_hours: float = 24.0


class MLConfig(BaseModel):
    enabled: bool = True
    model_dir: str = "models"
    batch_interval_readings: int = 20
    cloud_sync: CloudSyncConfig = Field(default_factory=CloudSyncConfig)


class APIConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])
    workers: int = 1


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "%(asctime)s | %(name)-28s | %(levelname)-8s | %(message)s"
    file_path: str = "logs/vide.log"
    max_size_bytes: int = 104857600
    backup_count: int = 5


class Settings(BaseModel):
    """Root configuration model. All nested configs validated via Pydantic."""

    factory: dict[str, str] = Field(default_factory=lambda: {"id": "factory-001", "name": "Default Factory", "timezone": "UTC"})
    communication: CommunicationConfig = Field(default_factory=CommunicationConfig)
    sensors: dict[str, SensorTypeConfig] = Field(default_factory=dict)
    equipment: dict = Field(default_factory=dict)
    fault_detection: FaultDetectionConfig = Field(default_factory=FaultDetectionConfig)
    maintenance: MaintenanceConfig = Field(default_factory=MaintenanceConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    ml: MLConfig = Field(default_factory=MLConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    api: APIConfig = Field(default_factory=APIConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @property
    def thresholds(self) -> ThresholdConfig:
        return ThresholdConfig(**self.equipment.get("thresholds", {}))

    @property
    def equipment_categories(self) -> dict[str, EquipmentCategory]:
        cats = {}
        for key, val in self.equipment.get("categories", {}).items():
            cats[key] = EquipmentCategory(**val)
        return cats

    def get_maintenance_interval(self, category: str) -> float:
        cats = self.equipment_categories
        if category in cats:
            return cats[category].default_maintenance_interval_hours
        return cats.get("custom", EquipmentCategory()).default_maintenance_interval_hours

    def get_default_rated_load(self, category: str) -> float:
        cats = self.equipment_categories
        if category in cats:
            return cats[category].default_rated_load_kw
        return cats.get("custom", EquipmentCategory()).default_rated_load_kw


# ── Singleton loader with thread-safe reload ─────────────────────────────────

_config_path: Path = Path("config.yaml")
_settings: Optional[Settings] = None
_lock = threading.Lock()


def _load_yaml(path: Path) -> dict[str, Any]:
    """Read and parse YAML configuration file."""
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _apply_env_overrides(raw: dict[str, Any]) -> dict[str, Any]:
    """Override sensitive values from environment variables."""
    env_map = {
        "communication.mqtt.broker": "VIDE_MQTT_BROKER",
        "communication.mqtt.port": "VIDE_MQTT_PORT",
        "communication.mqtt.username": "VIDE_MQTT_USER",
        "communication.mqtt.password": "VIDE_MQTT_PASS",
        "storage.database.path": "VIDE_DB_PATH",
        "llm.api_url": "VIDE_LLM_API_URL",
        "llm.model": "VIDE_LLM_MODEL",
    }
    for dot_path, env_var in env_map.items():
        val = os.environ.get(env_var)
        if val is not None:
            keys = dot_path.split(".")
            node = raw
            for k in keys[:-1]:
                node = node.setdefault(k, {})
            node[keys[-1]] = val
    return raw


def _build_settings() -> Settings:
    """Construct Settings from YAML + env overrides."""
    raw = _load_yaml(_config_path)
    raw = _apply_env_overrides(raw)
    return Settings(**raw)


def get_settings() -> Settings:
    """Return the singleton Settings instance. Loads on first call."""
    global _settings
    if _settings is None:
        with _lock:
            if _settings is None:
                _settings = _build_settings()
    return _settings


def reload_settings() -> Settings:
    """Force re-read the YAML file and rebuild Settings. Thread-safe."""
    global _settings
    with _lock:
        _settings = _build_settings()
    return _settings


def set_config_path(path: str | Path) -> None:
    """Change the config file path before first load."""
    global _config_path, _settings
    with _lock:
        _config_path = Path(path)
        _settings = None