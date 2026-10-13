"""How an attack command will be carried out: from where, after which move, with which outcome."""
from __future__ import annotations

from dataclasses import dataclass

from beastborn.domain.position import Position
from beastborn.engine.results import AttackResult
from beastborn.game.pathfinding import PathInfo


@dataclass(frozen=True)
class AttackPlan:
    """An attack the active player's unit can make with one ``AttackCommand``.

    If the target is out of range, the unit first moves along ``move`` to ``origin`` and attacks from there.
    """

    attacker_id: int
    target_id: int
    origin: Position  # tile the attack is made from
    move: PathInfo | None  # None = attack from where the unit stands
    result: AttackResult  # exact outcome of the hit, made from ``origin``

    @property
    def needs_move(self) -> bool:
        return self.move is not None

    @property
    def move_cost(self) -> int:
        return self.move.cost if self.move is not None else 0
