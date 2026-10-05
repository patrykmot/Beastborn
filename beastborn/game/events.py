"""Events emitted by the Game State Machine. Frontends use them for logs and animations."""
from __future__ import annotations

from dataclasses import dataclass

from beastborn.domain.effects import EffectKind
from beastborn.domain.position import Position
from beastborn.engine.results import AttackResult


@dataclass(frozen=True)
class TurnStarted:
    player: int
    round: int


@dataclass(frozen=True)
class EnergyRegenerated:
    unit_id: int
    before: int
    after: int


@dataclass(frozen=True)
class EffectTicked:
    unit_id: int
    kind: EffectKind
    amount: int
    hp_after: int


@dataclass(frozen=True)
class UnitMoved:
    unit_id: int
    path: tuple[Position, ...]  # includes start and destination
    cost: int
    energy_after: int


@dataclass(frozen=True)
class UnitAttacked:
    attacker_id: int
    target_id: int
    result: AttackResult
    target_hp_after: int
    energy_after: int


@dataclass(frozen=True)
class UnitDied:
    unit_id: int
    owner: int
    name: str


@dataclass(frozen=True)
class PlayerEliminated:
    player: int


@dataclass(frozen=True)
class TurnEnded:
    player: int


@dataclass(frozen=True)
class GameOver:
    winner: int | None


GameEvent = (
    TurnStarted | EnergyRegenerated | EffectTicked | UnitMoved | UnitAttacked
    | UnitDied | PlayerEliminated | TurnEnded | GameOver
)
