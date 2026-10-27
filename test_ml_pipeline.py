"""
Quick test: load the bootstrap model and run it on two sample readings —
one normal, one an obvious overload — to see it actually predict something.

Runs the REAL rule engine first (fault_detection/rules.py), then feeds
those rule evaluations + the raw reading into the ML model — this is
exactly what happens in production (services/reading_service.py). The
model was trained to lean on rule evaluations as a primary signal (per
spec: "ML should consume all rule evaluations"), so skipping this step
and only calling the ML model in isolation is misleading, not just
incomplete.

Run from the repo root: python test_ml_pipeline.py
"""

from ml.registry import ModelRegistry
from ml.features import build_feature_vector
from ml.explainer import explain_prediction
from fault_detection.rules import compute_derived, RuleContext, ALL_RULES
from training.feature_pipeline import _DetectionAdapter


def evaluate_rules(reading, ctx):
    """Same evaluation loop the real engine runs — DB-free, so this test
    script works standalone without a live database."""
    evals = {}
    for rule_id, rule_fn in ALL_RULES.items():
        evals[rule_id] = rule_fn(reading, ctx, _DEFAULT_THRESHOLDS, True)
    return evals


class _DEFAULT_THRESHOLDS:
    voltage_deviation_pct = 10.0
    voltage_critical_pct = 15.0
    frequency_deviation_hz = 0.5
    frequency_critical_hz = 1.0
    frequency_rocof_hz_per_s = 0.2
    overload_pct = 15.0
    overload_critical_pct = 30.0
    underload_pct = 70.0
    power_factor_min = 0.75
    power_factor_min_load_pct = 40.0
    reactive_to_apparent_ratio = 0.6
    reactive_min_power_kw = 1.0
    trend_increase_pct = 10.0
    trend_min_stability_ratio = 0.1
    sensor_min_power_for_validation = 0.5
    sensor_min_voltage_for_power = 50.0


def test_reading(model, label, reading):
    ctx = RuleContext(rolling_power=[reading["power_kw"]], rolling_voltage=[reading["voltage_v"]],
                       prev_frequency=50.0, rolling_mean_power=reading["power_kw"],
                       rolling_std_power=0.0, rolling_mean_voltage=reading["voltage_v"])

    rule_evals = evaluate_rules(reading, ctx)
    triggered = {k: v for k, v in rule_evals.items() if v is not None}
    rule_verdict = max(triggered.items(), key=lambda kv: kv[1].confidence)[1].type if triggered else "normal (no rule fired)"

    derived = compute_derived(reading["power_kw"], reading["voltage_v"],
                               reading["current_amps"], reading["rated_load_kw"])
    adapter = _DetectionAdapter(derived_metrics=derived, context=ctx, all_rule_evaluations=rule_evals)

    x = build_feature_vector(reading, adapter, reading["machine_type"])
    pred = model.predict_one(x)
    explanation = explain_prediction(model.fault_type_model, x)

    print(f"\n--- {label} ---")
    print("rule engine says:", rule_verdict)
    print("ML fault_type:", pred["fault_type"], f"({pred['fault_type_confidence'] * 100:.0f}% confidence)")
    print("ML anomaly probability:", f"{pred['anomaly_probability_next_window'] * 100:.0f}%")
    print("hours to maintenance:", round(pred["hours_to_maintenance"], 1))
    print("top factors:", explanation["top_factors"][:3])


if __name__ == "__main__":
    registry = ModelRegistry("models")
    registry.activate_version("v1_bootstrap")
    model = registry.load_current()
    print("Loaded model version:", model.version)

    # Normal motor reading — power well within rated load
    test_reading(model, "NORMAL READING", {
        "machine_name": "motor_1", "machine_type": "motor", "rated_load_kw": 10.0,
        "voltage_v": 230.0, "current_amps": 38.5, "power_kw": 8.2,
        "power_factor": 0.92, "frequency_hz": 50.0,
    })

    # Overloaded motor reading — power well above rated
    test_reading(model, "OVERLOAD READING", {
        "machine_name": "motor_1", "machine_type": "motor", "rated_load_kw": 10.0,
        "voltage_v": 228.0, "current_amps": 68.0, "power_kw": 14.5,
        "power_factor": 0.88, "frequency_hz": 50.0,
    })