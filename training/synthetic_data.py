"""
Generates synthetic sensor readings + weak labels for bootstrap
pretraining, before any real field data exists.

Runs entirely against fault_detection/rules.py directly (no database, no
FastAPI) so this can execute standalone in a Colab notebook. Physics is
kept honest: fault scenarios are built by perturbing the exact fields
each rule checks, using the exact thresholds from config.yaml, so the
rule engine's own judgement becomes the weak label source — same
principle as ml/labels.py, just applied offline to synthetic data
instead of live readings.

hours_to_maintenance targets reuse the same stress-penalty formula as
services/maintenance_service.py — the best physics-informed proxy
available before real failure data exists. The ML model's job during
bootstrap is to learn to approximate/refine that formula from raw
features; it gets progressively replaced by real outcomes over time.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import numpy as np

from fault_detection.rules import ALL_RULES, RuleContext, compute_derived
from ml.schema import FAULT_TYPE_CLASSES, MACHINE_TYPES

# Mirrors equipment.categories in config.yaml — kept local so this script
# has zero dependency on config/settings.py or a config.yaml file on disk.
EQUIPMENT_PROFILES = {
    "motor": {"rated_load_kw": 10.0, "base_interval_hours": 2000},
    "pump": {"rated_load_kw": 7.5, "base_interval_hours": 1500},
    "compressor": {"rated_load_kw": 15.0, "base_interval_hours": 1800},
    "generator": {"rated_load_kw": 50.0, "base_interval_hours": 2500},
    "conveyor": {"rated_load_kw": 5.0, "base_interval_hours": 3000},
    "hvac": {"rated_load_kw": 20.0, "base_interval_hours": 4000},
    "custom": {"rated_load_kw": 10.0, "base_interval_hours": 2000},
}

DEFAULT_THRESHOLDS = dict(
    voltage_deviation_pct=10.0, voltage_critical_pct=15.0,
    frequency_deviation_hz=0.5, frequency_critical_hz=1.0, frequency_rocof_hz_per_s=0.2,
    overload_pct=15.0, overload_critical_pct=30.0, underload_pct=70.0,
    power_factor_min=0.75, power_factor_min_load_pct=40.0,
    reactive_to_apparent_ratio=0.6, reactive_min_power_kw=1.0,
    trend_increase_pct=10.0, trend_min_stability_ratio=0.1,
    sensor_min_power_for_validation=0.5, sensor_min_voltage_for_power=50.0,
)


class _Thresholds:
    """Lightweight stand-in for config.settings.ThresholdConfig — plain attrs."""
    def __init__(self, **kw):
        self.__dict__.update(kw)


@dataclass
class SyntheticSample:
    reading: dict
    machine_type: str
    context: RuleContext
    all_rule_evaluations: dict
    primary_fault_rule_id: str  # "normal" if none
    hours_to_maintenance: float
    anomaly_next_window: int
    fault_type_idx: int


def _evaluate_rules(reading: dict, ctx: RuleContext, thresholds) -> dict:
    """Same evaluation loop as fault_detection.engine.detect(), DB-free."""
    evals = {}
    for rule_id, rule_fn in ALL_RULES.items():
        try:
            evals[rule_id] = rule_fn(reading, ctx, thresholds, True)
        except Exception:
            evals[rule_id] = None
    return evals


def _make_baseline_reading(machine_type: str, load_factor: float, rng: random.Random) -> dict:
    profile = EQUIPMENT_PROFILES[machine_type]
    rated = profile["rated_load_kw"]
    power_kw = rated * load_factor
    voltage_v = rng.gauss(230.0, 3.0)
    power_factor = rng.uniform(0.85, 0.98)
    current_amps = (power_kw * 1000) / (voltage_v * power_factor) if voltage_v > 0 else 0
    frequency_hz = rng.gauss(50.0, 0.03)
    return {
        "machine_name": f"synthetic_{machine_type}", "machine_type": machine_type,
        "rated_load_kw": rated, "voltage_v": round(voltage_v, 2),
        "current_amps": round(current_amps, 2), "power_kw": round(power_kw, 3),
        "power_factor": round(power_factor, 3), "frequency_hz": round(frequency_hz, 3),
        "nominal_voltage": 230.0,
    }


def _scale_current_with_power(r: dict, new_power_kw: float) -> float:
    """Keep S=VI roughly consistent with the new P so we don't accidentally
    trip sensor_anomaly (apparent_power < active_power) instead of the
    fault we're actually trying to inject."""
    old_power = max(r["power_kw"], 0.01)
    return round(r["current_amps"] * (new_power_kw / old_power), 2)


def _mechanical_overload(r: dict, rng: random.Random) -> dict:
    new_power = round(r["rated_load_kw"] * rng.uniform(1.2, 1.6), 3)
    return {**r, "power_kw": new_power, "current_amps": _scale_current_with_power(r, new_power)}


def _low_power_factor(r: dict, rng: random.Random) -> dict:
    new_power = round(r["rated_load_kw"] * rng.uniform(0.5, 0.9), 3)
    new_pf = round(rng.uniform(0.4, 0.7), 3)
    # I = P / (V * PF) — recompute current directly so S=VI stays consistent
    # with both the new power AND the degraded power factor.
    new_current = round((new_power * 1000) / (r["voltage_v"] * new_pf), 2) if r["voltage_v"] else r["current_amps"]
    return {**r, "power_kw": new_power, "power_factor": new_pf, "current_amps": new_current}


def _underload(r: dict, rng: random.Random) -> dict:
    new_power = round(r["rated_load_kw"] * rng.uniform(0.05, 0.3), 3)
    return {**r, "power_kw": new_power, "current_amps": _scale_current_with_power(r, new_power)}


def _recompute_current(r: dict) -> float:
    """I = P / (V * PF) — keeps S=VI consistent after voltage/PF changes so
    we don't accidentally trip sensor_anomaly instead of the intended fault."""
    if not r["voltage_v"] or not r["power_factor"]:
        return r["current_amps"]
    return round((r["power_kw"] * 1000) / (r["voltage_v"] * r["power_factor"]), 2)


def _voltage_sag(r: dict, rng: random.Random) -> dict:
    new_r = {**r, "voltage_v": round(rng.uniform(178, 205), 2)}
    new_r["current_amps"] = _recompute_current(new_r)
    return new_r


def _voltage_swell(r: dict, rng: random.Random) -> dict:
    new_r = {**r, "voltage_v": round(rng.uniform(255, 280), 2)}
    new_r["current_amps"] = _recompute_current(new_r)
    return new_r


_FAULT_INJECTORS = {
    "voltage_sag": _voltage_sag,
    "voltage_swell": _voltage_swell,
    "frequency_instability": lambda r, rng: {**r, "frequency_hz": round(rng.choice([-1, 1]) * rng.uniform(0.6, 1.5) + 50.0, 3)},
    "mechanical_overload": _mechanical_overload,
    "low_power_factor": _low_power_factor,
    "excessive_reactive_power": lambda r, rng: {**r, "current_amps": round(r["current_amps"] * rng.uniform(1.8, 2.5), 2)},
    "underload": _underload,
    "sensor_anomaly": lambda r, rng: {**r, "voltage_v": round(rng.uniform(0, 30), 2), "power_kw": round(r["rated_load_kw"] * rng.uniform(0.6, 1.0), 3)},
}
# mechanical_degradation is NOT in this dict — it's a multi-reading trend,
# not a single-reading perturbation. See _make_degradation_sample below.


def _make_degradation_sample(machine_type: str, rng: random.Random, thresholds: _Thresholds) -> SyntheticSample:
    """
    mechanical_degradation needs a LOW-VARIANCE rising trend across the
    rolling window — that can't come from perturbing one reading. Build a
    tight 10-reading synthetic history instead, then evaluate the final
    reading against the context that history produces.
    """
    profile = EQUIPMENT_PROFILES[machine_type]
    rated = profile["rated_load_kw"]
    start_load = rng.uniform(0.55, 0.7)
    total_increase = rng.uniform(0.15, 0.3)  # 15-30% rise, comfortably over trend_increase_pct=10%

    history_powers = []
    for step in range(9):
        load = start_load * (1 + total_increase * step / 9) + rng.gauss(0, 0.003)
        history_powers.append(rated * load)

    final_load = start_load * (1 + total_increase) + rng.gauss(0, 0.003)
    final_power = rated * final_load
    voltage_v = rng.gauss(230.0, 1.5)
    power_factor = rng.uniform(0.88, 0.96)
    current_amps = (final_power * 1000) / (voltage_v * power_factor)

    reading = {
        "machine_name": f"synthetic_{machine_type}", "machine_type": machine_type,
        "rated_load_kw": rated, "voltage_v": round(voltage_v, 2),
        "current_amps": round(current_amps, 2), "power_kw": round(final_power, 3),
        "power_factor": round(power_factor, 3), "frequency_hz": round(rng.gauss(50.0, 0.02), 3),
        "nominal_voltage": 230.0,
    }

    ctx = RuleContext(
        rolling_power=history_powers, rolling_voltage=[230.0] * 9, prev_frequency=50.0,
        rolling_mean_power=float(np.mean(history_powers)), rolling_std_power=float(np.std(history_powers)),
        rolling_mean_voltage=230.0,
    )

    evals = _evaluate_rules(reading, ctx, thresholds)
    triggered = {k: v for k, v in evals.items() if v is not None}
    primary_rule_id = max(triggered, key=lambda k: triggered[k].confidence) if triggered else "normal"

    hours = max(0.0, profile["base_interval_hours"] - 50.0)  # trend flags as a real anomaly

    return SyntheticSample(
        reading=reading, machine_type=machine_type, context=ctx, all_rule_evaluations=evals,
        primary_fault_rule_id=primary_rule_id, hours_to_maintenance=hours, anomaly_next_window=1,
        fault_type_idx=FAULT_TYPE_CLASSES.index(primary_rule_id),
    )


def generate_dataset(n_per_machine_type: int = 400, fault_ratio: float = 0.35, seed: int = 42) -> list[SyntheticSample]:
    rng = random.Random(seed)
    thresholds = _Thresholds(**DEFAULT_THRESHOLDS)
    samples: list[SyntheticSample] = []

    for machine_type in MACHINE_TYPES:
        profile = EQUIPMENT_PROFILES[machine_type]
        rolling_history: list[float] = []

        for i in range(n_per_machine_type):
            inject_fault = rng.random() < fault_ratio
            # Kept away from the underload_pct=70% threshold so normal and
            # non-underload fault samples don't accidentally co-trigger
            # underload (which has a flat confidence=85 and would win as
            # primary over lower-confidence intended faults).
            load_factor = rng.uniform(0.72, 0.95)
            reading = _make_baseline_reading(machine_type, load_factor, rng)
            fault_rule_id = "normal"

            if inject_fault:
                fault_rule_id = rng.choice(list(_FAULT_INJECTORS.keys()))
                reading = _FAULT_INJECTORS[fault_rule_id](reading, rng)

            ctx = RuleContext(
                rolling_power=rolling_history[-10:] or [reading["power_kw"]],
                rolling_voltage=[230.0] * min(len(rolling_history), 10) or [230.0],
                prev_frequency=50.0,
                rolling_mean_power=float(np.mean(rolling_history[-10:])) if rolling_history else reading["power_kw"],
                rolling_std_power=float(np.std(rolling_history[-10:])) if len(rolling_history) > 1 else 0.0,
                rolling_mean_voltage=230.0,
            )
            rolling_history.append(reading["power_kw"])

            evals = _evaluate_rules(reading, ctx, thresholds)
            triggered = {k: v for k, v in evals.items() if v is not None}
            primary_rule_id = max(triggered, key=lambda k: triggered[k].confidence) if triggered else "normal"

            # hours_to_maintenance target — same stress-penalty logic as MaintenanceService
            anomaly_penalty = 50.0 if triggered else 0.0
            overload_penalty = max(0.0, (load_factor * 100 - 80) * 10)
            hours = max(0.0, profile["base_interval_hours"] - anomaly_penalty - overload_penalty)

            samples.append(SyntheticSample(
                reading=reading, machine_type=machine_type, context=ctx, all_rule_evaluations=evals,
                primary_fault_rule_id=primary_rule_id, hours_to_maintenance=hours,
                anomaly_next_window=1 if triggered else 0,
                fault_type_idx=FAULT_TYPE_CLASSES.index(primary_rule_id),
            ))

        # Dedicated mechanical_degradation trend cases — ~8% of this
        # machine type's sample count, can't come from the injector loop above.
        for _ in range(max(8, int(n_per_machine_type * 0.08))):
            samples.append(_make_degradation_sample(machine_type, rng, thresholds))

    rng.shuffle(samples)
    return samples
