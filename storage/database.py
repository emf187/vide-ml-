"""
Database connection management and schema migrations.

Uses SQLite in WAL mode for concurrent read/write support.
All operations are thread-safe via Python's GIL on SQLite connections.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from config.settings import get_settings

logger = logging.getLogger(__name__)

_local = threading.local()
_db_initialized = False
_init_lock = threading.Lock()


def _db_path() -> Path:
    cfg = get_settings()
    p = Path(cfg.storage.database.path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def get_connection() -> sqlite3.Connection:
    """
    Return a thread-local SQLite connection.
    Each thread gets its own connection — safe for concurrent web requests
    and background data acquisition tasks.
    """
    conn: Optional[sqlite3.Connection] = getattr(_local, "conn", None)
    if conn is None:
        path = _db_path()
        conn = sqlite3.connect(str(path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        _local.conn = conn
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply all schema migrations idempotently."""

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS machines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            machine_name TEXT NOT NULL UNIQUE,
            machine_type TEXT NOT NULL DEFAULT 'custom',
            location TEXT NOT NULL DEFAULT 'unknown',
            rated_load_kw REAL NOT NULL DEFAULT 10.0,
            registered_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS sensor_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            machine_name TEXT NOT NULL,
            source_type TEXT NOT NULL DEFAULT 'unknown',
            voltage_v REAL,
            current_amps REAL,
            power_kw REAL,
            power_factor REAL DEFAULT 1.0,
            frequency_hz REAL DEFAULT 50.0,
            apparent_power_kva REAL DEFAULT 0.0,
            reactive_power_kvar REAL DEFAULT 0.0,
            energy_kwh REAL DEFAULT 0.0,
            temperature_c REAL,
            vibration_mm_s REAL,
            acceleration_g REAL,
            pressure_bar REAL,
            flow_lpm REAL,
            -- computed fields
            load_factor_pct REAL DEFAULT 0.0,
            is_running INTEGER DEFAULT 0,
            is_anomaly INTEGER DEFAULT 0,
            anomaly_types TEXT,
            diagnosis_json TEXT,
            -- metadata
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
            date TEXT NOT NULL DEFAULT (date('now')),
            hour INTEGER NOT NULL DEFAULT 0,
            day_of_week TEXT NOT NULL DEFAULT '',
            is_weekend INTEGER DEFAULT 0,
            FOREIGN KEY (machine_name) REFERENCES machines(machine_name)
        );

        CREATE TABLE IF NOT EXISTS maintenance_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            machine_name TEXT NOT NULL,
            maintenance_date TEXT NOT NULL,
            maintenance_type TEXT NOT NULL,
            hours_at_maintenance REAL NOT NULL DEFAULT 0,
            parts_replaced TEXT DEFAULT 'none',
            technician TEXT DEFAULT 'unknown',
            cost REAL DEFAULT 0,
            next_due_hours REAL DEFAULT 2000,
            notes TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (machine_name) REFERENCES machines(machine_name)
        );

        CREATE TABLE IF NOT EXISTS ml_predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            machine_name TEXT NOT NULL,
            hours_to_maintenance REAL,
            anomaly_probability_next_window REAL,
            fault_type TEXT,
            fault_type_confidence REAL,
            model_version TEXT,
            feature_importance_json TEXT,
            top_factors_json TEXT,
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (machine_name) REFERENCES machines(machine_name)
        );

        CREATE TABLE IF NOT EXISTS critical_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            machine_name TEXT NOT NULL,
            reading_id INTEGER,
            rule_id TEXT NOT NULL,
            fault_type TEXT NOT NULL,
            description TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            confirmed INTEGER,
            technician TEXT,
            notes TEXT,
            flagged_at TEXT NOT NULL DEFAULT (datetime('now')),
            resolved_at TEXT,
            FOREIGN KEY (machine_name) REFERENCES machines(machine_name),
            FOREIGN KEY (reading_id) REFERENCES sensor_readings(id)
        );

        CREATE INDEX IF NOT EXISTS idx_readings_machine_time
            ON sensor_readings(machine_name, timestamp);
        CREATE INDEX IF NOT EXISTS idx_readings_anomaly
            ON sensor_readings(is_anomaly, timestamp);
        CREATE INDEX IF NOT EXISTS idx_readings_date
            ON sensor_readings(date);
        CREATE INDEX IF NOT EXISTS idx_readings_hour_running
            ON sensor_readings(hour, is_running);
        CREATE INDEX IF NOT EXISTS idx_maint_machine
            ON maintenance_log(machine_name, maintenance_date);
        CREATE INDEX IF NOT EXISTS idx_ml_predictions_machine_time
            ON ml_predictions(machine_name, timestamp);
        CREATE INDEX IF NOT EXISTS idx_critical_events_status
            ON critical_events(status, flagged_at);
    """)

    # Add any columns that may not exist in older schemas
    _ensure_columns(conn, "sensor_readings", {
        "temperature_c": "REAL",
        "vibration_mm_s": "REAL",
        "acceleration_g": "REAL",
        "pressure_bar": "REAL",
        "flow_lpm": "REAL",
        "source_type": "TEXT NOT NULL DEFAULT 'unknown'",
        "diagnosis_json": "TEXT",
    })


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    """Add missing columns to a table without failing if they exist."""
    cursor = conn.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cursor.fetchall()}
    for col_name, col_type in columns.items():
        if col_name not in existing:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")
                logger.info("Added column %s.%s", table, col_name)
            except sqlite3.OperationalError:
                pass


def init_db() -> None:
    """
    Initialize the database schema. Called once at startup.
    Thread-safe via lock.
    """
    global _db_initialized
    if _db_initialized:
        return
    with _init_lock:
        if _db_initialized:
            return
        conn = get_connection()
        try:
            _migrate(conn)
            conn.commit()
            _db_initialized = True
            logger.info("Database initialized at %s", _db_path())
        except Exception:
            conn.rollback()
            logger.exception("Database initialization failed")
            raise