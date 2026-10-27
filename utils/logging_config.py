"""
Logging configuration.

Sets up rotating file handler and console output
based on config.yaml settings.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

from config.settings import get_settings


def setup_logging() -> None:
    cfg = get_settings()
    log_cfg = cfg.logging

    # Root logger
    root = logging.getLogger()
    root.setLevel(getattr(logging, log_cfg.level.upper(), logging.INFO))

    # Remove existing handlers to avoid duplicates on reload
    root.handlers.clear()

    # Console handler
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(log_cfg.format))
    root.addHandler(console)

    # File handler (rotating)
    log_path = log_cfg.file_path
    log_dir = os.path.dirname(log_path)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    try:
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=log_cfg.max_size_bytes,
            backupCount=log_cfg.backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(getattr(logging, log_cfg.level.upper(), logging.INFO))
        file_handler.setFormatter(logging.Formatter(log_cfg.format))
        root.addHandler(file_handler)
    except OSError as e:
        logging.warning("Cannot create log file %s: %s", log_path, e)

    # Suppress noisy libraries
    logging.getLogger("paho.mqtt").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)