"""
Builds rich context for LLM queries from live system state.

The LLM receives:
  - Real-time sensor readings
  - Fault detection engine outputs (all rule evaluations)
  - Historical anomaly patterns
  - Maintenance predictions
  - Machine configuration and thresholds
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from config.settings import get_settings
from fault_detection.engine import FaultDetectionEngine  # type: ignore[import]
from fusion.schema import FusedDiagnosis
from storage.repositories import (
    MachineRepository, ReadingRepository, MaintenanceRepository,
)

logger = logging.getLogger(__name__)


class ContextBuilder:
    """Assembles system context for LLM consumption."""

    def __init__(self) -> None:
        self._engine = FaultDetectionEngine()
        self._cfg = get_settings()

    def build_general_context(self) -> str:
        """Build context for a general system status query."""
        sections = []

        # System summary
        summary = ReadingRepository.get_summary()
        sections.append(
            f"## System Summary\n"
            f"- Total machines tracked: {summary.get('machine_count', 0)}\n"
            f"- Total energy recorded: {summary.get('total_energy', 0):.2f} kWh\n"
            f"- Total anomalies detected: {summary.get('total_anomalies', 0)}\n"
            f"- Average load factor: {summary.get('avg_load', 0):.1f}%\n"
        )

        # Recent anomalies
        anomalies = ReadingRepository.get_anomalies(limit=self._cfg.llm.context.max_recent_anomalies)
        if anomalies:
            anom_lines = []
            for a in anomalies:
                diag = a.get("diagnosis", {})
                anom_lines.append(
                    f"  - [{a['timestamp']}] {a['machine_name']}: "
                    f"{diag.get('type', 'Unknown')} (confidence {diag.get('confidence', '?')}) "
                    f"— Power: {a.get('power_kw', '?')}kW, Voltage: {a.get('voltage_v', '?')}V"
                )
            sections.append(f"## Recent Anomalies\n" + "\n".join(anom_lines))

        # Machine list with health
        machines = MachineRepository.list_all()
        if machines:
            machine_lines = []
            for m in machines:
                stats = ReadingRepository.get_machine_stats(m["machine_name"])
                machine_lines.append(
                    f"  - {m['machine_name']} ({m['machine_type']}): "
                    f"{stats['total_hours_run']:.1f}h run, "
                    f"{stats['anomaly_count_30d']} anomalies (30d), "
                    f"avg load {stats['avg_load_factor']:.1f}%, "
                    f"voltage stability {stats['voltage_stability_score']:.0f}%"
                )
            sections.append(f"## Machines\n" + "\n".join(machine_lines))

        return "\n\n".join(sections)

    def build_machine_context(self, machine_name: str) -> str:
        """Build context for a specific machine query."""
        sections = []

        # Machine profile
        machine = MachineRepository.get(machine_name)
        if not machine:
            return f"Machine '{machine_name}' not found in the system."

        sections.append(
            f"## Machine Profile\n"
            f"- Name: {machine['machine_name']}\n"
            f"- Type: {machine['machine_type']}\n"
            f"- Location: {machine['location']}\n"
            f"- Rated Load: {machine['rated_load_kw']} kW\n"
        )

        # Stats
        stats = ReadingRepository.get_machine_stats(machine_name)
        sections.append(
            f"## Operational Statistics\n"
            f"- Total hours run: {stats['total_hours_run']:.1f}h\n"
            f"- Anomalies (30d): {stats['anomaly_count_30d']}\n"
            f"- Average load factor: {stats['avg_load_factor']:.1f}%\n"
            f"- Voltage stability score: {stats['voltage_stability_score']:.0f}/100\n"
            f"- I²T thermal stress (1h): {stats['i2t_thermal_stress_1h']:.4f}\n"
        )

        # Maintenance prediction
        from services.maintenance_service import MaintenanceService
        maint_svc = MaintenanceService()
        pred = maint_svc.predict(machine_name)
        if pred:
            sections.append(
                f"## Maintenance Prediction\n"
                f"- Base interval: {pred['base_interval_hours']}h\n"
                f"- Adjusted interval: {pred['adjusted_interval_hours']:.0f}h\n"
                f"- Hours remaining: {pred['hours_remaining']:.1f}h\n"
                f"- Days remaining: {pred['days_remaining']:.1f}\n"
                f"- Recommended date: {pred['recommended_maintenance_date']}\n"
                f"- Urgency: {pred['urgency']}\n"
                f"- Stress factors: {json.dumps(pred['stress_factors'])}\n"
            )

        # Recent readings
        history = ReadingRepository.get_machine_history(machine_name, limit=self._cfg.llm.context.max_recent_readings)
        if history:
            read_lines = []
            for r in history[-10:]:  # Last 10 for brevity
                read_lines.append(
                    f"  - [{r['timestamp']}] P={r.get('power_kw', '?')}kW "
                    f"V={r.get('voltage_v', '?')}V I={r.get('current_amps', '?')}A "
                    f"PF={r.get('power_factor', '?')} LF={r.get('load_factor_pct', '?')}% "
                    f"{'⚠️ ANOMALY' if r.get('is_anomaly') else '✓'}"
                )
            sections.append(f"## Recent Readings (last 10)\n" + "\n".join(read_lines))

        # Recent anomalies for this machine
        machine_anomalies = ReadingRepository.get_anomalies_for_machine(machine_name, limit=5)
        if machine_anomalies:
            anom_lines = []
            for a in machine_anomalies:
                diag_str = a.get("diagnosis_json", "")
                try:
                    diag = json.loads(diag_str) if diag_str else {}
                except (json.JSONDecodeError, TypeError):
                    diag = {}
                anom_lines.append(
                    f"  - [{a['timestamp']}] {diag.get('type', 'Unknown')}: {diag.get('description', 'No details')}"
                )
            sections.append(f"## Recent Anomalies for this Machine\n" + "\n".join(anom_lines))

        # Maintenance history
        maint_history = MaintenanceRepository.get_history(machine_name)
        if maint_history:
            maint_lines = []
            for m in maint_history[:5]:
                maint_lines.append(
                    f"  - [{m['maintenance_date']}] {m['maintenance_type']} "
                    f"at {m['hours_at_maintenance']}h, parts: {m['parts_replaced']}, "
                    f"by {m['technician']}, cost: {m['cost']}"
                )
            sections.append(f"## Maintenance History\n" + "\n".join(maint_lines))

        return "\n\n".join(sections)

    def build_reading_diagnosis_context(self, reading: dict, detection_result, fused: FusedDiagnosis) -> str:
        """
        Build context from a specific reading, its rule engine output, and
        the fused ML+rules diagnosis. `detection_result` supplies rolling
        window detail (RuleContext) that isn't part of FusedDiagnosis;
        everything about the ML prediction and rule/ML agreement comes
        from `fused`.
        """
        sections = []

        dm = detection_result.derived_metrics
        ctx = detection_result.context

        sections.append(
            f"## Incoming Reading\n"
            f"- Machine: {reading.get('machine_name')}\n"
            f"- Power: {reading.get('power_kw')} kW\n"
            f"- Voltage: {reading.get('voltage_v')} V\n"
            f"- Current: {reading.get('current_amps')} A\n"
            f"- Power Factor: {reading.get('power_factor')}\n"
            f"- Frequency: {reading.get('frequency_hz')} Hz\n"
            f"- Rated Load: {reading.get('rated_load_kw')} kW\n"
            f"- Source: {reading.get('source_type', 'unknown')}\n"
        )

        sections.append(
            f"## Derived Metrics\n"
            f"- Apparent Power: {dm.get('apparent_power_kva', 0):.3f} kVA\n"
            f"- Reactive Power: {dm.get('reactive_power_kvar', 0):.3f} kVAR\n"
            f"- Load Factor: {dm.get('load_factor_pct', 0):.1f}%\n"
        )

        sections.append(
            f"## Rolling Context (last {len(ctx.rolling_power)} readings)\n"
            f"- Mean Power: {ctx.rolling_mean_power:.3f} kW\n"
            f"- Std Power: {ctx.rolling_std_power:.3f} kW\n"
            f"- Mean Voltage: {ctx.rolling_mean_voltage:.1f} V\n"
            f"- Previous Frequency: {ctx.prev_frequency:.2f} Hz\n"
        )

        # All rule evaluations — this is the key exposure for LLM
        rule_lines = []
        for rule_id, fault in detection_result.all_rule_evaluations.items():
            if fault:
                rule_lines.append(
                    f"  - **{rule_id}**: TRIGGERED — {fault.type} "
                    f"(confidence {fault.confidence}%, urgency {fault.urgency})\n"
                    f"    Description: {fault.description}\n"
                    f"    Action: {fault.action}"
                )
            else:
                rule_lines.append(f"  - {rule_id}: Not triggered")
        sections.append(f"## Fault Detection Rule Evaluation\n" + "\n".join(rule_lines))

        if detection_result.primary_fault:
            pf = detection_result.primary_fault
            sections.append(
                f"## Primary Diagnosis\n"
                f"- Type: {pf.type}\n"
                f"- Confidence: {pf.confidence}%\n"
                f"- Urgency: {pf.urgency}\n"
                f"- Description: {pf.description}\n"
                f"- Recommended Action: {pf.action}\n"
            )
        else:
            sections.append("## Primary Diagnosis\nNormal operation — no faults detected.")

        # ML prediction, when the batched inference cycle produced one this reading
        if fused.ml_prediction:
            mp = fused.ml_prediction
            sections.append(
                f"## ML Prediction (model {mp.model_version})\n"
                f"- Predicted fault type: {mp.fault_type} ({mp.fault_type_confidence * 100:.0f}% confidence)\n"
                f"- Anomaly probability (next window): {mp.anomaly_probability_next_window * 100:.0f}%\n"
                f"- Hours to maintenance: {mp.hours_to_maintenance:.0f}h\n"
                f"- Top contributing factors: {', '.join(mp.top_factors) if mp.top_factors else 'n/a'}\n"
            )
            sections.append(
                f"## Rule/ML Agreement: {fused.agreement}\n"
                + ("The rule engine and ML model point to the same issue." if fused.agreement == "agree"
                   else "The rule engine and ML model disagree — treat as worth a second look, not settled."
                   if fused.agreement == "disagree" else "Only one of rules/ML produced a signal this cycle.")
            )
        else:
            sections.append("## ML Prediction\nNo ML prediction available this cycle (batched — runs every N readings).")

        # Thresholds in effect
        t = self._cfg.thresholds
        sections.append(
            f"## Active Thresholds\n"
            f"- Voltage deviation: ±{t.voltage_deviation_pct}% (critical: ±{t.voltage_critical_pct}%)\n"
            f"- Frequency deviation: ±{t.frequency_deviation_hz}Hz (critical: ±{t.frequency_critical_hz}Hz)\n"
            f"- Overload: +{t.overload_pct}% (critical: +{t.overload_critical_pct}%)\n"
            f"- Underload: <{t.underload_pct}% of rated\n"
            f"- Min power factor: {t.power_factor_min} (above {t.power_factor_min_load_pct}% load)\n"
            f"- Reactive/apparent ratio: >{t.reactive_to_apparent_ratio}\n"
        )

        return "\n\n".join(sections)