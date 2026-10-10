"""Interaction -> JSON render model for the browser. Read-only: nothing is recomputed here."""
from __future__ import annotations

from typing import Any

from beastborn.constance import LOG_LINES
from beastborn.domain.board import Board
from beastborn.domain.position import Position
from beastborn.domain.terrain import Tile
from beastborn.domain.unit import UnitStats
from beastborn.engine.results import AttackResult
from beastborn.game.pathfinding import PathInfo
from beastborn.game.view import GameView, UnitView
from beastborn.ui.interaction import AttackAnimation, Interaction

JSON = dict[str, Any]


def unit_json(unit: UnitView) -> JSON:
    s: UnitStats = unit.stats
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


def render(game_id: str, ui: Interaction) -> JSON:
    view: GameView = ui.view
    board: Board = view.board
    return {
        "game_id": game_id,
        "seed": view.seed,
        "round": view.round,
        "phase": view.phase.value,
        "active_player": view.active_player,
        "winner": view.winner,
        "human_turn": ui.human_turn,
        "board": {"width": board.width, "height": board.height, "tiles": _tiles_json(board)},
        "players": [
            {"index": p.index, "name": p.name, "eliminated": p.eliminated, "units": len(view.units_of(p.index))}
            for p in view.players
        ],
        "units": [unit_json(u) for u in view.units],
        "selection": {
            "unit_id": ui.selected,
            "reachable": [_reachable_json(pos, info) for pos, info in sorted(ui.reachable.items())],
            "targets": [_target_json(uid, result) for uid, result in sorted(ui.targets.items())],
        },
        # attacks since the last intent, for animations; "seq" lets a reloaded page skip ones it already saw
        "actions": {"seq": ui.action_seq, "attacks": [_attack_json(a) for a in ui.last_attacks]},
        "message": {"text": ui.message, "error": ui.message_is_error},
        "log": list(ui.log)[-LOG_LINES:],
    }


def _xy(pos: Position) -> list[int]:
    return [pos.x, pos.y]


def _tiles_json(board: Board) -> list[list[JSON]]:
    return [[_tile_json(board.tile(Position(x, y))) for x in range(board.width)] for y in range(board.height)]


def _tile_json(tile: Tile) -> JSON:
    return {"terrain": tile.terrain.value, "elevation": tile.elevation}


def _reachable_json(pos: Position, info: PathInfo) -> JSON:
    return {"x": pos.x, "y": pos.y, "cost": info.cost, "path": [_xy(p) for p in info.path]}


def _target_json(unit_id: int, result: AttackResult) -> JSON:
    return {"unit_id": unit_id, "damage": result.damage, "formula": result.formula(), "blocked": result.blocked}


def _attack_json(a: AttackAnimation) -> JSON:
    return {
        "seq": a.seq,
        "attacker_id": a.attacker_id,
        "target_id": a.target_id,
        "attacker_type": a.attacker_type,
        "owner": a.owner,
        "from": _xy(a.source),
        "to": _xy(a.target),
        "ranged": a.ranged,
        "damage": a.damage,
        "killed": a.killed,
    }
