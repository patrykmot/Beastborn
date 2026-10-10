"""Creating a new game: map, players, armies."""
from __future__ import annotations

import random

from beastborn import constance as C
from beastborn.domain.board import Board
from beastborn.domain.player import Player
from beastborn.domain.position import Position
from beastborn.domain.roster import Roster, load_roster
from beastborn.domain.unit import UnitState, UnitStats
from beastborn.engine.interface import CalculationEngine
from beastborn.engine.standard_engine import StandardCalculationEngine
from beastborn.game.config import GameConfig
from beastborn.game.map_generator import corner_positions, generate_board, spawn_positions
from beastborn.game.state import GameState
from beastborn.game.state_machine import GameStateMachine

Placement = tuple[int, UnitStats, tuple[int, int]]  # (owner, stats, (x, y))


def new_game(
    num_players: int = C.DEFAULT_PLAYERS,
    seed: int | None = None,
    config: GameConfig | None = None,
    engine: CalculationEngine | None = None,
    roster: Roster | None = None,
    player_names: list[str] | None = None,
    start: bool = True,
) -> GameStateMachine:
    """Random map + Boss in each corner + ``units_per_player`` random recruits per Boss.

    The same seed always produces the same map and armies.
    """
    config = config or GameConfig()
    engine = engine or StandardCalculationEngine()
    roster = roster or load_roster()
    if seed is None:
        seed = random.SystemRandom().randrange(C.SEED_RANGE)
    rng: random.Random = random.Random(seed)

    corners: list[Position] = corner_positions(config.width, config.height, num_players)  # validates player count
    board: Board = generate_board(config, num_players, rng)
    armies: list[list[UnitStats]] = [
        [roster.boss] + [rng.choice(roster.recruits) for _ in range(config.units_per_player)]
        for _ in range(num_players)
    ]
    units: dict[int, UnitState] = place_armies(board, corners, armies, engine)
    state: GameState = GameState(
        board=board, players=make_players(num_players, player_names), units=units, config=config, seed=seed
    )
    return _create_machine(state, engine, start)


def custom_game(
    board: Board,
    placements: list[Placement],
    num_players: int = C.DEFAULT_PLAYERS,
    engine: CalculationEngine | None = None,
    config: GameConfig | None = None,
    start: bool = True,
) -> GameStateMachine:
    """A game on a hand-made board: ``placements`` = [(owner, stats, (x, y)), ...]. For tests and scenarios."""
    engine = engine or StandardCalculationEngine()
    units: dict[int, UnitState] = {
        unit_id: make_unit(unit_id, owner, stats, Position(*xy), engine)
        for unit_id, (owner, stats, xy) in enumerate(placements, start=1)
    }
    state: GameState = GameState(
        board=board, players=make_players(num_players), units=units, config=config or GameConfig()
    )
    return _create_machine(state, engine, start)


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
