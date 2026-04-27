"""Mapping confidence dynamics — typed-event API.

Single source of truth for updating mapping confidence_score and deriving
confidence_level.  Never set confidence_score directly — always go through
``apply()`` with a typed event.

Confidence level thresholds (derived from score):
  >= 0.85  →  HIGH
  >= 0.50  →  MEDIUM
  else     →  LOW
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Union

from finance.coretax.db import _utcnow


# ── Thresholds ──────────────────────────────────────────────────────────────

HIGH_THRESHOLD = 0.85
MEDIUM_THRESHOLD = 0.50
CONFIRM_FLOOR = 0.95
RUN_USED_BOOST = 0.05
RUN_UNUSED_DECAY = 0.10
STALE_YEAR_THRESHOLD = 2  # unused for >= 2 reconcile runs spanning >= 1 year


# ── Events ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Confirmed:
    """User confirmed a mapping in the Mapping tab."""
    pass


@dataclass(frozen=True)
class RunUsed:
    """Mapping fired successfully in a reconcile run."""
    tax_year: int


@dataclass(frozen=True)
class RunUnused:
    """Mapping was not used in a reconcile run.
    Only penalise if fingerprint is still present in PWM universe."""
    tax_year: int
    fingerprint_still_present: bool = True


@dataclass(frozen=True)
class TargetDeleted:
    """The target CoreTax row was deleted or superseded."""
    pass


Event = Union[Confirmed, RunUsed, RunUnused, TargetDeleted]


# ── Public API ──────────────────────────────────────────────────────────────

def derive_level(score: float) -> str:
    """Derive confidence_level from confidence_score."""
    if score >= HIGH_THRESHOLD:
        return "HIGH"
    if score >= MEDIUM_THRESHOLD:
        return "MEDIUM"
    return "LOW"


def apply(event: Event, mapping: dict) -> dict:
    """Apply an event to a mapping dict, returning updated fields.

    The caller is responsible for persisting the returned dict fields to the DB.
    Returns a dict of fields to update (confidence_score, confidence_level,
    times_confirmed, years_used, last_used_at, updated_at).
    """
    now = _utcnow()
    score = float(mapping.get("confidence_score", 1.0))
    times_confirmed = int(mapping.get("times_confirmed", 0))
    years_used = int(mapping.get("years_used", 0))
    last_used_at = mapping.get("last_used_at")

    if isinstance(event, Confirmed):
        times_confirmed += 1
        score = max(score, CONFIRM_FLOOR)

    elif isinstance(event, RunUsed):
        years_used += 1
        score = min(1.0, score + RUN_USED_BOOST)
        last_used_at = now

    elif isinstance(event, RunUnused):
        if event.fingerprint_still_present:
            # Decay only if fingerprint is still in PWM universe
            score = max(0.0, score - RUN_UNUSED_DECAY)

    elif isinstance(event, TargetDeleted):
        score = 0.15  # drops to LOW immediately

    else:
        raise ValueError(f"Unknown event type: {type(event)}")

    return {
        "confidence_score": round(score, 4),
        "confidence_level": derive_level(score),
        "times_confirmed": times_confirmed,
        "years_used": years_used,
        "last_used_at": last_used_at,
        "updated_at": now,
    }
