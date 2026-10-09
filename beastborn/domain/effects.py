"""Status effects."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EffectKind(Enum):
    VENOM = "venom"  # damage over time
    ACID = "acid"  # permanent armour shred on hit


@dataclass(frozen=True)
class EffectSpec:
    """An on-hit effect a unit's attack carries (defined in unit data).

    Its strength scales with the attacker's ATK. That is a rule, so the numbers
    live in ``engine.rules_config.RulesConfig`` (see docs/game_mechanics.md, section 6).
    """

    kind: EffectKind


@dataclass(frozen=True)
class StatusEffect:
    """An effect currently active on a unit."""

    kind: EffectKind
    magnitude: int
    remaining_turns: int
