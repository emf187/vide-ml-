"""
Individual fault detection rules.

Each rule is a pure function: (reading, context, thresholds) -> Optional[Fault]
No side effects, no I/O — fully testable and configurable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from config.settings import ThresholdConfig


@dataclass
class Fault:
    """Represents a single detected fault."""
    rule_id: str
    type: str
    confidence: int
    description: str
    action: str
    urgency: str  # "critical", "high", "medium", "low", "none"
    derived_metrics: dict[str, float] = field(default_factory=dict)


@dataclass
class RuleContext:
    """Rolling window context passed to each rule."""
    rolling_power: list[float]
    rolling_voltage: list[float]
    prev_frequency: float
    rolling_mean_power: float
    rolling_std_power: float
    rolling_mean_voltage: float

    @classmethod
    def from_recent(cls, recent_readings: list[dict]) -> "RuleContext":
        powers = [r.get("power_kw", 0) or 0 for r in recent_readings]
        voltages = [r.get("voltage_v", 0) or 0 for r in recent_readings]
        frequencies = [r.get("frequency_hz", 50) or 50 for r in recent_readings]

        mean_p = float(np.mean(powers)) if powers else 0.0
        std_p = float(np.std(powers)) if len(powers) > 1 else 0.0
        mean_v = float(np.mean(voltages)) if voltages else 0.0
        prev_f = frequencies[0] if frequencies else 50.0

        return cls(
            rolling_power=powers,
            rolling_voltage=voltages,
            prev_frequency=prev_f,
            rolling_mean_power=mean_p,
            rolling_std_power=std_p,
            rolling_mean_voltage=mean_v,
        )


def compute_derived(power_kw: float, voltage_v: float, current_amps: float,
                    rated_load_kw: float) -> dict[str, float]:
    """Compute apparent/reactive power and load factor."""
    s_kva = (voltage_v * current_amps) / 1000.0 if (voltage_v and current_amps) else 0.0
    q_sq = max(0.0, s_kva ** 2 - power_kw ** 2)
    q_kvar = float(np.sqrt(q_sq))
    load_factor = (power_kw / rated_load_kw) if rated_load_kw > 0 else 0.0
    return {
        "apparent_power_kva": round(s_kva, 4),
        "reactive_power_kvar": round(q_kvar, 4),
        "load_factor_pct": round(load_factor * 100, 2),
    }


def check_sensor_anomaly(
    reading: dict, ctx: RuleContext, t: ThresholdConfig, enabled: bool
) -> Optional[Fault]:
    if not enabled:
        return None

    power_kw = reading.get("power_kw", 0) or 0
    voltage_v = reading.get("voltage_v", 0) or 0
    current_amps = reading.get("current_amps", 0) or 0
    dm = compute_derived(power_kw, voltage_v, current_amps, reading.get("rated_load_kw", 10))

    # Active power exceeds apparent power
    if dm["apparent_power_kva"] < power_kw and power_kw > t.sensor_min_power_for_validation:
        return Fault(
            rule_id="sensor_anomaly_power_gt_apparent",
            type="Sensor Anomaly",
            confidence=99,
            description=f"Active power ({power_kw}kW) exceeds apparent power ({dm['apparent_power_kva']:.2f}kVA). CT clamp misconfigured or phase error.",
            action="Verify current transformer wiring and phase alignment.",
            urgency="high",
            derived_metrics=dm,
        )

    # Non-zero power with impossible voltage
    if voltage_v < t.sensor_min_voltage_for_power and power_kw > t.sensor_min_power_for_validation:
        return Fault(
            rule_id="sensor_anomaly_low_voltage_power",
            type="Sensor Anomaly",
            confidence=95,
            description=f"Non-zero power ({power_kw}kW) reported with impossible voltage ({voltage_v}V). Sensor communication error.",
            action="Inspect device wiring. Check for damaged voltage dividers or loose connections.",
            urgency="high",
            derived_metrics=dm,
        )

    return None


def check_voltage_sag(
    reading: dict, ctx: RuleContext, t: ThresholdConfig, enabled: bool
) -> Optional[Fault]:
    if not enabled:
        return None

    voltage_v = reading.get("voltage_v", 0) or 0
    nominal = reading.get("nominal_voltage", 230.0) or 230.0
    lower_limit = nominal * (1 - t.voltage_deviation_pct / 100)
    critical_limit = nominal * (1 - t.voltage_critical_pct / 100)

    if critical_limit < voltage_v < lower_limit:
        conf = min(99, int((nominal - voltage_v) * 2))
        return Fault(
            rule_id="voltage_sag",
            type="Voltage Sag / Grid Undervoltage",
            confidence=conf,
            description=f"Voltage at {voltage_v}V (nominal {nominal}V). Motor compensating with higher current, risking thermal overload.",
            action="Check distribution transformer tap settings. Reduce mechanical load to prevent motor burnout.",
            urgency="medium",
            derived_metrics={},
        )

    if voltage_v <= critical_limit and voltage_v >= t.sensor_min_voltage_for_power:
        conf = min(99, int((nominal - voltage_v) * 2))
        return Fault(
            rule_id="voltage_sag_critical",
            type="Voltage Sag / Critical Undervoltage",
            confidence=conf,
            description=f"Critical voltage at {voltage_v}V (nominal {nominal}V). Immediate risk of motor stall and equipment damage.",
            action="Emergency: reduce load immediately. Check grid supply and transformer health.",
            urgency="critical",
            derived_metrics={},
        )

    return None


def check_voltage_swell(
    reading: dict, ctx: RuleContext, t: ThresholdConfig, enabled: bool
) -> Optional[Fault]:
    if not enabled:
        return None

    voltage_v = reading.get("voltage_v", 0) or 0
    nominal = reading.get("nominal_voltage", 230.0) or 230.0
    upper_limit = nominal * (1 + t.voltage_deviation_pct / 100)
    critical_limit = nominal * (1 + t.voltage_critical_pct / 100)

    if upper_limit < voltage_v <= critical_limit:
        conf = min(99, int((voltage_v - nominal) * 2))
        return Fault(
            rule_id="voltage_swell",
            type="Voltage Swell / Overvoltage",
            confidence=conf,
            description=f"Voltage at {voltage_v}V (nominal {nominal}V). Risks insulation breakdown in motor windings and VFD damage.",
            action="Check transformer tap changer. Disconnect non-critical loads to protect sensitive equipment.",
            urgency="medium",
            derived_metrics={},
        )

    if voltage_v > critical_limit:
        conf = min(99, int((voltage_v - nominal) * 2))
        return Fault(
            rule_id="voltage_swell_critical",
            type="Voltage Swell / Critical Overvoltage",
            confidence=conf,
            description=f"Critical overvoltage at {voltage_v}V. Immediate insulation failure risk.",
            action="Emergency: isolate equipment. Check for neutral fault or regulator failure.",
            urgency="critical",
            derived_metrics={},
        )

    return None


def check_frequency_instability(
    reading: dict, ctx: RuleContext, t: ThresholdConfig, enabled: bool
) -> Optional[Fault]:
    if not enabled:
        return None

    freq = reading.get("frequency_hz", 50) or 50
    nominal_f = 50.0
    deviation = abs(freq - nominal_f)
    rocof = abs(freq - ctx.prev_frequency)

    if deviation <= t.frequency_deviation_hz and rocof <= t.frequency_rocof_hz_per_s:
        return None

    conf = min(95, int(deviation * 50 + rocof * 100))
    urgency = "critical" if deviation > t.frequency_critical_hz else "high"

    return Fault(
        rule_id="frequency_instability",
        type="Frequency Instability",
        confidence=conf,
        description=f"Frequency at {freq}Hz (deviation: {deviation:.2f}Hz, RoCoF: {rocof:.2f}Hz/s). Indicates grid overload or generator governor failure.",
        action="Check generator fuel supply and governor linkages. Verify grid frequency from utility.",
        urgency=urgency,
        derived_metrics={"frequency_deviation_hz": round(deviation, 3), "rocof_hz_per_s": round(rocof, 3)},
    )


def check_mechanical_overload(
    reading: dict, ctx: RuleContext, t: ThresholdConfig, enabled: bool
) -> Optional[Fault]:
    if not enabled:
        return None

    power_kw = reading.get("power_kw", 0) or 0
    rated = reading.get("rated_load_kw", 0) or 0
    if rated <= 0:
        return None

    overload_pct = (power_kw / rated - 1) * 100
    if overload_pct <= t.overload_pct:
        return None

    conf = min(99, 75 + int((overload_pct - t.overload_pct) * 2))
    urgency = "critical" if overload_pct > t.overload_critical_pct else "high"

    return Fault(
        rule_id="mechanical_overload",
        type="Mechanical Overload",
        confidence=conf,
        description=f"Active power {power_kw}kW exceeds rated {rated}kW by {overload_pct:.1f}%.",
        action="Immediately reduce mechanical load. Check for jams, bearing seizures, or process blockages.",
        urgency=urgency,
        derived_metrics={"overload_pct": round(overload_pct, 2)},
    )


def check_low_power_factor(
    reading: dict, ctx: RuleContext, t: ThresholdConfig, enabled: bool
) -> Optional[Fault]:
    if not enabled:
        return None

    pf = reading.get("power_factor", 1.0) or 1.0
    rated = reading.get("rated_load_kw", 0) or 0
    power_kw = reading.get("power_kw", 0) or 0
    load_factor = (power_kw / rated) if rated > 0 else 0

    if pf >= t.power_factor_min or load_factor < (t.power_factor_min_load_pct / 100):
        return None

    pf_drop = (0.85 - pf) * 100
    conf = min(95, 60 + int(pf_drop * 3))

    return Fault(
        rule_id="low_power_factor",
        type="Low Power Factor / Winding Degradation",
        confidence=conf,
        description=f"Power factor at {pf:.2f} under {load_factor * 100:.0f}% load. Indicates stator winding shorts or core degradation.",
        action="Schedule insulation resistance (megger) test. Check for inter-turn shorts in stator windings.",
        urgency="high",
        derived_metrics={"power_factor": round(pf, 3), "load_factor_pct": round(load_factor * 100, 1)},
    )


def check_excessive_reactive_power(
    reading: dict, ctx: RuleContext, t: ThresholdConfig, enabled: bool
) -> Optional[Fault]:
    if not enabled:
        return None

    power_kw = reading.get("power_kw", 0) or 0
    voltage_v = reading.get("voltage_v", 0) or 0
    current_amps = reading.get("current_amps", 0) or 0
    rated = reading.get("rated_load_kw", 10)

    dm = compute_derived(power_kw, voltage_v, current_amps, rated)
    s_kva = dm["apparent_power_kva"]
    q_kvar = dm["reactive_power_kvar"]

    if s_kva <= 0 or power_kw < t.reactive_min_power_kw:
        return None

    ratio = q_kvar / s_kva
    if ratio <= t.reactive_to_apparent_ratio:
        return None

    conf = min(90, int(ratio * 100))

    return Fault(
        rule_id="excessive_reactive_power",
        type="Excessive Reactive Power / Core Saturation",
        confidence=conf,
        description=f"Reactive power {q_kvar:.2f}kVAR is {ratio * 100:.0f}% of apparent power. Core saturation likely.",
        action="Inspect core laminations for damage. Install power factor correction capacitors.",
        urgency="medium",
        derived_metrics=dm,
    )


def check_mechanical_degradation(
    reading: dict, ctx: RuleContext, t: ThresholdConfig, enabled: bool
) -> Optional[Fault]:
    if not enabled:
        return None

    power_kw = reading.get("power_kw", 0) or 0
    if ctx.rolling_mean_power <= 0:
        return None

    stability_ratio = ctx.rolling_std_power / ctx.rolling_mean_power if ctx.rolling_mean_power > 0 else 999
    if stability_ratio > t.trend_min_stability_ratio:
        return None

    increase_pct = (power_kw / ctx.rolling_mean_power - 1) * 100
    if increase_pct <= t.trend_increase_pct:
        return None

    conf = min(90, 50 + int(increase_pct * 3))

    return Fault(
        rule_id="mechanical_degradation",
        type="Mechanical Degradation (Bearing Wear)",
        confidence=conf,
        description=f"Steady upward trend in power draw (+{increase_pct:.1f}% vs rolling baseline of {ctx.rolling_mean_power:.2f}kW).",
        action="Schedule vibration analysis. Inspect and grease bearings. Check alignment.",
        urgency="medium",
        derived_metrics={"trend_increase_pct": round(increase_pct, 2), "baseline_kw": round(ctx.rolling_mean_power, 3)},
    )


def check_underload(
    reading: dict, ctx: RuleContext, t: ThresholdConfig, enabled: bool
) -> Optional[Fault]:
    if not enabled:
        return None

    power_kw = reading.get("power_kw", 0) or 0
    current_amps = reading.get("current_amps", 0) or 0
    rated = reading.get("rated_load_kw", 0) or 0
    if rated <= 0:
        return None

    load_pct = (power_kw / rated) * 100
    if load_pct >= t.underload_pct or current_amps < 1.0:
        return None

    urgency = "medium" if load_pct < 20 else "low"

    return Fault(
        rule_id="underload",
        type="Underload / Dry Run / Belt Slip",
        confidence=85,
        description=f"Machine running at only {load_pct:.0f}% load with {current_amps}A draw.",
        action="Check fluid levels, belt tension, mechanical couplings, and material feed.",
        urgency=urgency,
        derived_metrics={"load_pct": round(load_pct, 1)},
    )


# Registry: rule_id -> check function
ALL_RULES = {
    "sensor_anomaly": check_sensor_anomaly,
    "voltage_sag": check_voltage_sag,
    "voltage_swell": check_voltage_swell,
    "frequency_instability": check_frequency_instability,
    "mechanical_overload": check_mechanical_overload,
    "low_power_factor": check_low_power_factor,
    "excessive_reactive_power": check_excessive_reactive_power,
    "mechanical_degradation": check_mechanical_degradation,
    "underload": check_underload,
}