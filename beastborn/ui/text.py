"""Human-readable text for events. Shared by all frontends."""
from __future__ import annotations

from beastborn.engine.results import AttackResult
from beastborn.game import events as ev
from beastborn.game.view import GameView, UnitView


def round_header(round_number: int, player_name: str) -> str:
    return f"--- Round {round_number}: {player_name} ---"


def owned_name(owner: int, name: str) -> str:
    """E.g. 'P1 Big Rat'."""
    return f"P{owner + 1} {name}"


def unit_label(view: GameView, unit_id: int) -> str:
    unit: UnitView | None = view.unit(unit_id)
    if unit is None:
        return f"#{unit_id}"
    return owned_name(unit.owner, unit.name)


def describe(event: ev.GameEvent, before: GameView, after: GameView) -> str | None:
    """One log line for an event, or None for events not worth logging.

    ``before`` is the view from before the command (dead units are still in it).
    """

    def label(unit_id: int) -> str:
        return unit_label(before if before.unit(unit_id) else after, unit_id)

    if isinstance(event, ev.TurnStarted):
        return round_header(event.round, after.players[event.player].name)
    if isinstance(event, ev.UnitMoved):
        return f"{label(event.unit_id)} moved {event.path[0]}->{event.path[-1]} (-{event.cost} EN)"
    if isinstance(event, ev.UnitAttacked):
        r: AttackResult = event.result
        verb: str = "shot" if r.ranged else "hit"
        prefix: str = f"{label(event.attacker_id)} {verb} {label(event.target_id)}"
        if r.blocked:
            return f"{prefix}: blocked"
        return f"{prefix} for {r.damage} [{r.formula()}], HP {event.target_hp_after}"
    if isinstance(event, ev.EffectTicked):
        return f"{label(event.unit_id)} takes {event.amount} {event.kind.value}, HP {event.hp_after}"
    if isinstance(event, ev.UnitDied):
        return f"{owned_name(event.owner, event.name)} died"
    if isinstance(event, ev.PlayerEliminated):
        return f"{after.players[event.player].name} is eliminated!"
    if isinstance(event, ev.GameOver):
        if event.winner is None:
            return "Game over - no winner"
        return f"{after.players[event.winner].name} wins!"
    return None  # EnergyRegenerated, TurnEnded: visible on the board already
