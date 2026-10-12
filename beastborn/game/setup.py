"""Creating a new game: map, players, armies."""
from __future__ import annotations

import random

from beastborn import constant as C
from beastborn.domain.board import Board
from beastborn.domain.player import Player
from beastborn.domain.position import Position
from beastborn.domain.roster import Roster, load_roster
from beastborn.domain.unit import UnitState, UnitStats
from beastborn.engine.interface import CalculationEngine
from beastborn.engine.standard_engine import StandardCalculationEngine
from beastborn.game.map_generator import corner_positions, generate_board, spawn_positions
from beastborn.game.state import GameState
from beastborn.game.state_machine import GameStateMachine

Placement = tuple[int, UnitStats, tuple[int, int]]  # (owner, stats, (x, y))


def new_game(
    num_players: int = C.DEFAULT_PLAYERS,
    seed: int | None = None,
    width: int = C.DEFAULT_MAP_SIZE,
    height: int = C.DEFAULT_MAP_SIZE,
    engine: CalculationEngine | None = None,
    roster: Roster | None = None,
    player_names: list[str] | None = None,
    start: bool = True,
) -> GameStateMachine:
    """Random ``width`` x ``height`` map + the same army (see ``make_army``) in each corner.

    The same seed always produces the same map.
    """
    engine = engine or StandardCalculationEngine()
    roster = roster or load_roster()
    if seed is None:
        seed = random.SystemRandom().randrange(C.SEED_RANGE)
    rng: random.Random = random.Random(seed)

    corners: list[Position] = corner_positions(width, height, num_players)  # validates player count
    board: Board = generate_board(width, height, num_players, rng)
    armies: list[list[UnitStats]] = [make_army(roster) for _ in range(num_players)]

    units: dict[int, UnitState] = place_armies(board, corners, armies, engine)
    state: GameState = GameState(
        board=board, players=make_players(num_players, player_names), units=units, seed=seed
    )
    return _create_machine(state, engine, start)


def custom_game(
    board: Board,
    placements: list[Placement],
    num_players: int = C.DEFAULT_PLAYERS,
    engine: CalculationEngine | None = None,
    start: bool = True,
) -> GameStateMachine:
    """A game on a hand-made board: ``placements`` = [(owner, stats, (x, y)), ...]. For tests and scenarios."""
    engine = engine or StandardCalculationEngine()
    units: dict[int, UnitState] = {
        unit_id: make_unit(unit_id, owner, stats, Position(*xy), engine)
        for unit_id, (owner, stats, xy) in enumerate(placements, start=1)
    }
    state: GameState = GameState(
        board=board, players=make_players(num_players), units=units
    )
    return _create_machine(state, engine, start)


def make_army(roster: Roster) -> list[UnitStats]:
    """Boss first (it gets the corner), then ``*_PER_PLAYER`` units of each kind (constant.py)."""
    counts: list[tuple[str, int]] = [
        (C.UNIT_BIG_RAT, C.BIG_RATS_PER_PLAYER),
        (C.UNIT_PEASANT, C.PEASANTS_PER_PLAYER),
        (C.UNIT_ARCHER, C.ARCHERS_PER_PLAYER),
    ]
    return [roster.boss] + [roster[key] for key, count in counts for _ in range(count)]


def make_players(num_players: int, names: list[str] | None = None) -> list[Player]:
    """``Player 1``, ``Player 2``... unless ``names`` are given."""
    names = names or [C.PLAYER_NAME_TEMPLATE.format(number=i + 1) for i in range(num_players)]
    return [Player(i, names[i]) for i in range(num_players)]


def place_armies(
    board: Board, corners: list[Position], armies: list[list[UnitStats]], engine: CalculationEngine
) -> dict[int, UnitState]:
    units: dict[int, UnitState] = {}
    taken: set[Position] = set()
    next_id: int = 1
    for owner, (corner, army) in enumerate(zip(corners, armies)):
        spots: list[Position] = spawn_positions(board, corner, len(army), taken)  # Boss gets the corner itself
        for stats, pos in zip(army, spots):
            units[next_id] = make_unit(next_id, owner, stats, pos, engine)
            taken.add(pos)
            next_id += 1
    return units


def make_unit(unit_id: int, owner: int, stats: UnitStats, pos: Position, engine: CalculationEngine) -> UnitState:
    return UnitState(
        id=unit_id,
        owner=owner,
        stats=stats,
        position=pos,
        hp=stats.hp,
        energy=engine.initial_energy(stats),
        defense=stats.defense,
    )


def _create_machine(state: GameState, engine: CalculationEngine, start: bool) -> GameStateMachine:
    gsm: GameStateMachine = GameStateMachine(state, engine)
    if start:
        gsm.start()
    return gsm
