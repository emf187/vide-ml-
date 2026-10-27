"""
Data access layer for sensor readings, machines, and maintenance records.

All methods accept and return plain dicts/lists — no ORM dependency.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

import numpy as np

from storage.database import get_connection

logger = logging.getLogger(__name__)


class MachineRepository:
    """CRUD operations for machine registry."""

    @staticmethod
    def register(
        machine_name: str,
        machine_type: str = "custom",
        location: str = "unknown",
        rated_load_kw: float = 10.0,
    ) -> dict:
        conn = get_connection()
        now = datetime.now().isoformat()
        try:
            conn.execute(
                """INSERT INTO machines (machine_name, machine_type, location, rated_load_kw, registered_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(machine_name) DO UPDATE SET
                       machine_type=excluded.machine_type,
                       location=excluded.location,
                       rated_load_kw=excluded.rated_load_kw,
                       updated_at=excluded.updated_at""",
                (machine_name, machine_type, location, rated_load_kw, now, now),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return MachineRepository.get(machine_name)

    @staticmethod
    def get(machine_name: str) -> Optional[dict]:
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM machines WHERE machine_name = ?", (machine_name,)
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def list_all() -> list[dict]:
        conn = get_connection()
        rows = conn.execute("SELECT * FROM machines ORDER BY machine_name").fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def delete(machine_name: str) -> bool:
        conn = get_connection()
        cursor = conn.execute(
            "DELETE FROM machines WHERE machine_name = ?", (machine_name,)
        )
        conn.commit()
        return cursor.rowcount > 0


class ReadingRepository:
    """Read/write operations for sensor readings."""

    @staticmethod
    def insert(reading: dict) -> int:
        """Insert a single sensor reading row. Returns the row id."""
        conn = get_connection()
        now = datetime.now()
        diagnosis_json = json.dumps(reading.get("diagnosis")) if reading.get("diagnosis") else None
        anomaly_types = json.dumps(reading.get("anomaly_types", []))

        try:
            cursor = conn.execute(
                """INSERT INTO sensor_readings (
                    machine_name, source_type,
                    voltage_v, current_amps, power_kw, power_factor, frequency_hz,
                    apparent_power_kva, reactive_power_kvar, energy_kwh,
                    temperature_c, vibration_mm_s, acceleration_g, pressure_bar, flow_lpm,
                    load_factor_pct, is_running, is_anomaly, anomaly_types, diagnosis_json,
                    timestamp, date, hour, day_of_week, is_weekend
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    reading["machine_name"],
                    reading.get("source_type", "unknown"),
                    reading.get("voltage_v"),
                    reading.get("current_amps"),
                    reading.get("power_kw"),
                    reading.get("power_factor", 1.0),
                    reading.get("frequency_hz", 50.0),
                    reading.get("apparent_power_kva", 0.0),
                    reading.get("reactive_power_kvar", 0.0),
                    reading.get("energy_kwh", 0.0),
                    reading.get("temperature_c"),
                    reading.get("vibration_mm_s"),
                    reading.get("acceleration_g"),
                    reading.get("pressure_bar"),
                    reading.get("flow_lpm"),
                    reading.get("load_factor_pct", 0.0),
                    reading.get("is_running", 0),
                    reading.get("is_anomaly", 0),
                    anomaly_types,
                    diagnosis_json,
                    reading.get("timestamp", now.isoformat()),
                    now.strftime("%Y-%m-%d"),
                    now.hour,
                    now.strftime("%A"),
                    1 if now.weekday() >= 5 else 0,
                ),
            )
            conn.commit()
            return cursor.lastrowid
        except Exception:
            conn.rollback()
            raise

    @staticmethod
    def get_recent(machine_name: str, limit: int = 10) -> list[dict]:
        conn = get_connection()
        rows = conn.execute(
            """SELECT power_kw, voltage_v, current_amps, frequency_hz, power_factor,
                      timestamp
               FROM sensor_readings
               WHERE machine_name = ? AND is_running = 1
               ORDER BY timestamp DESC LIMIT ?""",
            (machine_name, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def get_anomalies(limit: int = 20) -> list[dict]:
        conn = get_connection()
        rows = conn.execute(
            """SELECT machine_name, power_kw, voltage_v, current_amps,
                      anomaly_types, diagnosis_json, timestamp
               FROM sensor_readings
               WHERE is_anomaly = 1
               ORDER BY timestamp DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            if d.get("diagnosis_json"):
                try:
                    d["diagnosis"] = json.loads(d["diagnosis_json"])
                except (json.JSONDecodeError, TypeError):
                    d["diagnosis"] = None
            if d.get("anomaly_types"):
                try:
                    d["anomaly_types"] = json.loads(d["anomaly_types"])
                except (json.JSONDecodeError, TypeError):
                    d["anomaly_types"] = []
            del d["diagnosis_json"]
            results.append(d)
        return results

    @staticmethod
    def get_anomalies_for_machine(machine_name: str, limit: int = 20) -> list[dict]:
        conn = get_connection()
        rows = conn.execute(
            """SELECT * FROM sensor_readings
               WHERE machine_name = ? AND is_anomaly = 1
               ORDER BY timestamp DESC LIMIT ?""",
            (machine_name, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def get_machine_history(machine_name: str, limit: int = 200) -> list[dict]:
        conn = get_connection()
        rows = conn.execute(
            """SELECT timestamp, power_kw, voltage_v, current_amps,
                      load_factor_pct, energy_kwh, is_anomaly, anomaly_types,
                      temperature_c, vibration_mm_s
               FROM sensor_readings
               WHERE machine_name = ?
               ORDER BY timestamp DESC LIMIT ?""",
            (machine_name, limit),
        ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            if d.get("anomaly_types"):
                try:
                    d["anomaly_types"] = json.loads(d["anomaly_types"])
                except (json.JSONDecodeError, TypeError):
                    d["anomaly_types"] = []
            results.append(d)
        return results

    @staticmethod
    def get_heatmap() -> dict[str, list[float]]:
        conn = get_connection()
        rows = conn.execute(
            """SELECT machine_name, hour, ROUND(AVG(power_kw), 2) AS avg_power
               FROM sensor_readings
               WHERE is_running = 1
               GROUP BY machine_name, hour
               ORDER BY machine_name, hour"""
        ).fetchall()
        result: dict[str, list[float]] = {}
        for r in rows:
            m = r["machine_name"]
            if m not in result:
                result[m] = [0.0] * 24
            result[m][r["hour"]] = r["avg_power"]
        return result

    @staticmethod
    def get_daily_trend() -> list[dict]:
        conn = get_connection()
        rows = conn.execute(
            """SELECT date,
                      ROUND(SUM(energy_kwh), 2) AS total_kwh,
                      SUM(is_anomaly) AS anomalies
               FROM sensor_readings
               GROUP BY date ORDER BY date ASC"""
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def get_summary() -> dict:
        conn = get_connection()
        row = conn.execute(
            """SELECT
                   COALESCE(SUM(energy_kwh), 0) AS total_energy,
                   COALESCE(SUM(CASE WHEN is_running=1 THEN 1 ELSE 0 END), 0) AS total_running_readings,
                   COALESCE(SUM(is_anomaly), 0) AS total_anomalies,
                   COUNT(DISTINCT machine_name) AS machine_count,
                   COALESCE(AVG(CASE WHEN is_running=1 THEN load_factor_pct END), 0) AS avg_load
               FROM sensor_readings"""
        ).fetchone()
        return dict(row)

    @staticmethod
    def get_machine_stats(machine_name: str) -> dict:
        """Compute aggregated statistics for maintenance prediction."""
        conn = get_connection()
        thirty_days_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        one_hour_ago = (datetime.now() - timedelta(hours=1)).isoformat()

        # Total running hours (each reading represents ~5 seconds of run time)
        hours_row = conn.execute(
            "SELECT COALESCE(SUM(1.0/720.0), 0) AS total_hours FROM sensor_readings WHERE machine_name = ? AND is_running = 1",
            (machine_name,),
        ).fetchone()

        # Anomaly count last 30 days
        anomaly_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM sensor_readings WHERE machine_name = ? AND is_anomaly = 1 AND date >= ?",
            (machine_name, thirty_days_ago),
        ).fetchone()

        # Average load factor
        load_row = conn.execute(
            "SELECT AVG(load_factor_pct) AS avg_lf FROM sensor_readings WHERE machine_name = ? AND is_running = 1",
            (machine_name,),
        ).fetchone()

        # Voltage stability (coefficient of variation)
        v_row = conn.execute(
            """SELECT AVG(voltage_v) AS avg_v,
                      AVG(voltage_v * voltage_v) - AVG(voltage_v) * AVG(voltage_v) AS variance
               FROM sensor_readings
               WHERE machine_name = ? AND date >= ? AND voltage_v IS NOT NULL""",
            (machine_name, thirty_days_ago),
        ).fetchone()

        # I²T thermal proxy last hour
        current_rows = conn.execute(
            """SELECT current_amps FROM sensor_readings
               WHERE machine_name = ? AND timestamp >= ? AND is_running = 1 AND current_amps IS NOT NULL""",
            (machine_name, one_hour_ago),
        ).fetchall()

        total_hours = hours_row["total_hours"] if hours_row else 0
        anomaly_count = anomaly_row["cnt"] if anomaly_row else 0
        avg_load = load_row["avg_lf"] if load_row and load_row["avg_lf"] else 0

        voltage_stability = 100.0
        if v_row and v_row["avg_v"] and v_row["variance"] is not None:
            std = np.sqrt(max(0.0, v_row["variance"]))
            avg_v = v_row["avg_v"]
            if avg_v > 0:
                cv = (std / avg_v) * 100
                voltage_stability = max(0.0, 100.0 - cv)

        i2t = sum(c["current_amps"] ** 2 for c in current_rows) * (1.0 / 720.0) if current_rows else 0.0

        return {
            "total_hours_run": round(total_hours, 2),
            "anomaly_count_30d": anomaly_count,
            "avg_load_factor": round(avg_load, 2) if avg_load else 0.0,
            "voltage_stability_score": round(voltage_stability, 2),
            "i2t_thermal_stress_1h": round(i2t, 4),
        }


class MaintenanceRepository:
    """CRUD for maintenance records."""

    @staticmethod
    def record(entry: dict) -> int:
        conn = get_connection()
        now = datetime.now().isoformat()
        try:
            cursor = conn.execute(
                """INSERT INTO maintenance_log
                   (machine_name, maintenance_date, maintenance_type,
                    hours_at_maintenance, parts_replaced, technician,
                    cost, next_due_hours, notes, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry["machine_name"], now, entry["maintenance_type"],
                    entry.get("hours_at_maintenance", 0),
                    entry.get("parts_replaced", "none"),
                    entry.get("technician", "unknown"),
                    entry.get("cost", 0),
                    entry.get("next_due_hours", 2000),
                    entry.get("notes", ""),
                    now,
                ),
            )
            conn.commit()
            return cursor.lastrowid
        except Exception:
            conn.rollback()
            raise

    @staticmethod
    def get_history(machine_name: str) -> list[dict]:
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM maintenance_log WHERE machine_name = ? ORDER BY maintenance_date DESC",
            (machine_name,),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def get_last(machine_name: str) -> Optional[dict]:
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM maintenance_log WHERE machine_name = ? ORDER BY maintenance_date DESC LIMIT 1",
            (machine_name,),
        ).fetchone()
        return dict(row) if row else None


class MLPredictionRepository:
    """Read/write for ML layer predictions."""

    @staticmethod
    def insert(prediction: "MLPrediction") -> int:  # noqa: F821 (avoid import cycle; duck-typed)
        conn = get_connection()
        try:
            cursor = conn.execute(
                """INSERT INTO ml_predictions (
                    machine_name, hours_to_maintenance, anomaly_probability_next_window,
                    fault_type, fault_type_confidence, model_version,
                    feature_importance_json, top_factors_json, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    prediction.machine_name,
                    prediction.hours_to_maintenance,
                    prediction.anomaly_probability_next_window,
                    prediction.fault_type,
                    prediction.fault_type_confidence,
                    prediction.model_version,
                    json.dumps(prediction.feature_importance),
                    json.dumps(prediction.top_factors),
                    prediction.timestamp or datetime.now().isoformat(),
                ),
            )
            conn.commit()
            return cursor.lastrowid
        except Exception:
            conn.rollback()
            raise

    @staticmethod
    def get_latest(machine_name: str) -> Optional[dict]:
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM ml_predictions WHERE machine_name = ? ORDER BY timestamp DESC LIMIT 1",
            (machine_name,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["feature_importance"] = json.loads(d.pop("feature_importance_json") or "{}")
        d["top_factors"] = json.loads(d.pop("top_factors_json") or "[]")
        return d

    @staticmethod
    def get_history(machine_name: str, limit: int = 50) -> list[dict]:
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM ml_predictions WHERE machine_name = ? ORDER BY timestamp DESC LIMIT ?",
            (machine_name, limit),
        ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["feature_importance"] = json.loads(d.pop("feature_importance_json") or "{}")
            d["top_factors"] = json.loads(d.pop("top_factors_json") or "[]")
            results.append(d)
        return results


class CriticalEventRepository:
    """
    Engineer-in-the-loop confirmation queue for rule-engine critical flags.
    This is the ONLY source of trusted 'this was a real critical event'
    labels — nothing gets auto-labeled as a failure. See ml/labels.py.
    """

    @staticmethod
    def create_flag(machine_name: str, reading_id: Optional[int], rule_id: str, fault_type: str, description: str) -> int:
        conn = get_connection()
        try:
            cursor = conn.execute(
                """INSERT INTO critical_events
                   (machine_name, reading_id, rule_id, fault_type, description, status)
                   VALUES (?, ?, ?, ?, ?, 'pending')""",
                (machine_name, reading_id, rule_id, fault_type, description),
            )
            conn.commit()
            return cursor.lastrowid
        except Exception:
            conn.rollback()
            raise

    @staticmethod
    def confirm(event_id: int, confirmed: bool, technician: str, notes: str = "") -> bool:
        conn = get_connection()
        now = datetime.now().isoformat()
        status = "confirmed" if confirmed else "dismissed"
        cursor = conn.execute(
            """UPDATE critical_events
               SET status = ?, confirmed = ?, technician = ?, notes = ?, resolved_at = ?
               WHERE id = ?""",
            (status, 1 if confirmed else 0, technician, notes, now, event_id),
        )
        conn.commit()
        return cursor.rowcount > 0

    @staticmethod
    def get_pending(limit: int = 50) -> list[dict]:
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM critical_events WHERE status = 'pending' ORDER BY flagged_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def get_confirmed_for_training() -> list[dict]:
        """Highest-trust label source — used by training/feature_pipeline.py."""
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM critical_events WHERE status = 'confirmed' ORDER BY flagged_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]