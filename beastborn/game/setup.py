"""Creating a new game: map, players, armies."""
from __future__ import annotations

import random

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


def new_game(
    num_players: int = 2,
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
        seed = random.SystemRandom().randrange(1_000_000)
    rng = random.Random(seed)

    corners = corner_positions(config.width, config.height, num_players)  # validates player count
    board = generate_board(config, num_players, rng)
    names = player_names or [f"Player {i + 1}" for i in range(num_players)]
    players = [Player(i, names[i]) for i in range(num_players)]

    armies = [[roster.boss] + [rng.choice(roster.recruits) for _ in range(config.units_per_player)]
              for _ in range(num_players)]
    units = place_armies(board, corners, armies, engine)

    state = GameState(board=board, players=players, units=units, config=config, seed=seed)
    gsm = GameStateMachine(state, engine)
    if start:
        gsm.start()
    return gsm


def place_armies(board: Board, corners: list[Position], armies: list[list[UnitStats]], engine: CalculationEngine) -> dict[int, UnitState]:
    units: dict[int, UnitState] = {}
    taken: set[Position] = set()
    next_id = 1
    for owner, (corner, army) in enumerate(zip(corners, armies)):
        spots = spawn_positions(board, corner, len(army), taken)  # Boss gets the corner itself
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


def custom_game(
    board: Board,
    placements: list[tuple[int, UnitStats, tuple[int, int]]],
    num_players: int = 2,
    engine: CalculationEngine | None = None,
    config: GameConfig | None = None,
    start: bool = True,
) -> GameStateMachine:
    """A game on a hand-made board: ``placements`` = [(owner, stats, (x, y)), ...]. For tests and scenarios."""
    engine = engine or StandardCalculationEngine()
    units = {
        i: make_unit(i, owner, stats, Position(*xy), engine)
        for i, (owner, stats, xy) in enumerate(placements, start=1)
    }
    players = [Player(i, f"Player {i + 1}") for i in range(num_players)]
    state = GameState(board=board, players=players, units=units, config=config or GameConfig())
    gsm = GameStateMachine(state, engine)
    if start:
        gsm.start()
    return gsm
