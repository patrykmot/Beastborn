"""Interaction -> JSON render model for the browser. Read-only: nothing is recomputed here."""
from __future__ import annotations

from typing import Any

from beastborn.domain.position import Position
from beastborn.domain.terrain import Tile
from beastborn.game.view import UnitView
from beastborn.ui.interaction import AttackAnimation, Interaction

LOG_LINES = 50


def unit_json(unit: UnitView) -> dict[str, Any]:
    s = unit.stats
    return {
        "id": unit.id,
        "owner": unit.owner,
        "type": s.key,
        "name": s.name,
        "code": s.code,
        "boss": s.is_boss,
        "x": unit.position.x,
        "y": unit.position.y,
        "hp": unit.hp,
        "max_hp": s.hp,
        "energy": unit.energy,
        "max_en": s.max_en,
        "reg_en": s.reg_en,
        "atk": s.atk,
        "en_atk": s.en_atk,
        "def": unit.defense,
        "base_def": s.defense,
        "current_def": unit.current_defense,
        "range": s.attack_range,
        "move_penalty": s.move_penalty,
        "effects": [{"kind": e.kind.value, "magnitude": e.magnitude, "turns": e.remaining_turns} for e in unit.effects],
        "on_hit": [e.kind.value for e in s.on_hit],
    }


def render(game_id: str, ui: Interaction) -> dict[str, Any]:
    view = ui.view
    board = view.board
    tiles = [
        [_tile_json(board.tile(Position(x, y))) for x in range(board.width)]
        for y in range(board.height)
    ]

    reachable = [
        {"x": pos.x, "y": pos.y, "cost": info.cost, "path": [[p.x, p.y] for p in info.path]}
        for pos, info in sorted(ui.reachable.items())
    ]
    targets = [
        {"unit_id": uid, "damage": r.damage, "formula": r.formula(), "blocked": r.blocked}
        for uid, r in sorted(ui.targets.items())
    ]
    return {
        "game_id": game_id,
        "seed": view.seed,
        "round": view.round,
        "phase": view.phase.value,
        "active_player": view.active_player,
        "winner": view.winner,
        "human_turn": ui.human_turn,
        "board": {"width": board.width, "height": board.height, "tiles": tiles},
        "players": [
            {"index": p.index, "name": p.name, "eliminated": p.eliminated, "units": len(view.units_of(p.index))}
            for p in view.players
        ],
        "units": [unit_json(u) for u in view.units],
        "selection": {"unit_id": ui.selected, "reachable": reachable, "targets": targets},
        # attacks since the last intent, for animations; "seq" lets a reloaded page skip ones it already saw
        "actions": {"seq": ui.action_seq, "attacks": [_attack_json(a) for a in ui.last_attacks]},
        "message": {"text": ui.message, "error": ui.message_is_error},
        "log": list(ui.log)[-LOG_LINES:],
    }


def _tile_json(tile: Tile) -> dict[str, Any]:
    return {"terrain": tile.terrain.value, "elevation": tile.elevation}


def _attack_json(a: AttackAnimation) -> dict[str, Any]:
    return {
        "seq": a.seq,
        "attacker_id": a.attacker_id,
        "target_id": a.target_id,
        "attacker_type": a.attacker_type,
        "owner": a.owner,
        "from": [a.source.x, a.source.y],
        "to": [a.target.x, a.target.y],
        "ranged": a.ranged,
        "damage": a.damage,
        "killed": a.killed,
    }
