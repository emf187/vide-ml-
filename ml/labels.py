"""
Label sourcing for training.

There is no ground-truth "failure" field anywhere in the schema, and per
product decision, automated labeling of failures is intentionally NOT
done — that responsibility stays with engineers. Two label sources feed
the ML layer instead, ranked by trust:

  1. CONFIRMED  — an engineer explicitly confirmed a critical event via
                   the /api/ml/critical-events/{id}/confirm endpoint.
                   These are the highest-trust labels.
  2. WEAK        — the rule engine already flags urgency="critical" for
                   physically extreme readings (voltage_sag_critical,
                   frequency instability beyond critical_hz, overload
                   beyond critical_pct, etc — see fault_detection/rules.py).
                   Used for bootstrap training before enough confirmed
                   labels exist; down-weighted relative to confirmed ones.
"""

from __future__ import annotations

from typing import Optional

from fault_detection.engine import DetectionResult
from storage.repositories import CriticalEventRepository


def is_weak_critical(detection: DetectionResult) -> bool:
    """A rule fired with urgency='critical' — cheap, always-available label."""
    return detection.primary_fault is not None and detection.primary_fault.urgency == "critical"


def flag_for_review(machine_name: str, reading_id: int, detection: DetectionResult) -> Optional[int]:
    """
    Called from ReadingService right after a reading is stored. If the
    rule engine flagged urgency='critical', create a pending review
    record an engineer can confirm or dismiss later. Returns the new
    event id, or None if nothing critical was detected.
    """
    if not is_weak_critical(detection):
        return None

    fault = detection.primary_fault
    return CriticalEventRepository.create_flag(
        machine_name=machine_name,
        reading_id=reading_id,
        rule_id=fault.rule_id,
        fault_type=fault.type,
        description=fault.description,
    )


def confirm(event_id: int, confirmed: bool, technician: str, notes: str = "") -> None:
    """Engineer confirms (real critical event) or dismisses (false positive)."""
    CriticalEventRepository.confirm(event_id, confirmed=confirmed, technician=technician, notes=notes)
