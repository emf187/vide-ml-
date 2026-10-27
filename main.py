#!/usr/bin/env python3
"""
Vide Industrial — Production Entry Point

Usage:
    python main.py                    # Start with default config.yaml
    VIDE_MQTT_BROKER=10.0.0.5 python main.py  # Override MQTT broker
    python main.py --config /path/to/custom.yaml
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import uvicorn

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Vide Industrial — Fault Detection & Predictive Maintenance")
    parser.add_argument(
        "--config", "-c",
        type=str,
        default="config.yaml",
        help="Path to configuration YAML file (default: config.yaml)",
    )
    parser.add_argument(
        "--host", "-H",
        type=str,
        default=None,
        help="Override API host (default: from config)",
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=None,
        help="Override API port (default: from config)",
    )
    parser.add_argument(
        "--workers", "-w",
        type=int,
        default=None,
        help="Number of uvicorn workers (default: from config)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Set config path before any imports that use get_settings()
    os.environ.setdefault("VIDE_CONFIG_PATH", args.config)

    # Delayed import to ensure config path is set first
    from config.settings import set_config_path, get_settings
    set_config_path(args.config)

    cfg = get_settings()

    host = args.host or cfg.api.host
    port = args.port or cfg.api.port
    workers = args.workers or cfg.api.workers

    # LLM status check
    llm_key = os.environ.get(cfg.llm.api_key_env_var)
    llm_status = "configured" if llm_key else "not configured"

    # Data source status
    sources = []
    if cfg.communication.mqtt.enabled:
        sources.append(f"MQTT ({cfg.communication.mqtt.broker}:{cfg.communication.mqtt.port})")
    if cfg.communication.http.enabled and cfg.communication.http.pollers:
        sources.append(f"HTTP ({len(cfg.communication.http.pollers)} poller(s))")
    if cfg.communication.serial.enabled and cfg.communication.serial.ports:
        sources.append(f"Serial ({len(cfg.communication.serial.ports)} port(s))")

    print("=" * 64)
    print("  Vide Industrial v3.0 — Production")
    print("=" * 64)
    print(f"  Factory:      {cfg.factory.get('name', 'unnamed')} ({cfg.factory.get('id', '?')})")
    print(f"  API:          http://{host}:{port}")
    print(f"  Docs:         http://{host}:{port}/docs")
    print(f"  Database:     {cfg.storage.database.path}")
    print(f"  Data Sources: {', '.join(sources) if sources else 'none enabled'}")
    print(f"  LLM:          {llm_status} ({cfg.llm.provider} / {cfg.llm.model})")
    print(f"  Detection:    {'enabled' if cfg.fault_detection.enabled else 'disabled'}")
    print(f"  Workers:      {workers}")
    print("=" * 64)

    uvicorn.run(
        "api.app:create_app",
        host=host,
        port=port,
        workers=workers,
        factory=True,
        log_level="info",
    )


if __name__ == "__main__":
    main()