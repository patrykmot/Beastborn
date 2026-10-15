"""Events emitted by the Game State Machine. Frontends use them for logs and animations."""
from __future__ import annotations

from dataclasses import dataclass

from beastborn.domain.effects import EffectKind
from beastborn.domain.position import Position
from beastborn.engine.results import AttackResult


@dataclass(frozen=True)
class RoundStarted:
    round: int


@dataclass(frozen=True)
class TurnStarted:
    """``unit_id`` (of ``player``) gets its turn: it regenerates, its effects tick, then it may act."""

    player: int
    round: int
    unit_id: int


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
    source: Position  # attacker's tile when it hit (after a move made by the same command)
    target: Position  # target's tile


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
    """The active unit's turn is over: End turn, or it cannot move or attack any more."""

    player: int
    unit_id: int
    idle: bool  # the unit neither moved nor attacked in this turn


@dataclass(frozen=True)
class GameOver:
    winner: int | None


GameEvent = (
    RoundStarted | TurnStarted | EnergyRegenerated | EffectTicked | UnitMoved | UnitAttacked
    | UnitDied | PlayerEliminated | TurnEnded | GameOver
)
