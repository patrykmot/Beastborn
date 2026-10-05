"""Status effects."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EffectKind(Enum):
    VENOM = "venom"  # damage over time
    ACID = "acid"  # permanent armour shred on hit


@dataclass(frozen=True)
class EffectSpec:
    """An on-hit effect a unit's attack carries (defined in unit data)."""

    kind: EffectKind
    magnitude: int
    duration: int = 0  # turns; 0 for instant effects such as Acid


@dataclass(frozen=True)
class StatusEffect:
    """An effect currently active on a unit."""

    kind: EffectKind
    magnitude: int
    remaining_turns: int
