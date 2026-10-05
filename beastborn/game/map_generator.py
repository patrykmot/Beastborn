"""Random (seeded) battlefield generation.

RNG is allowed here: it only shapes the map. Combat itself has no randomness.
"""
from __future__ import annotations

import random

from beastborn.domain.board import Board
from beastborn.domain.position import Position
from beastborn.domain.terrain import TerrainType, Tile
from beastborn.game.config import MAX_PLAYERS, MIN_PLAYERS, GameConfig


def corner_positions(width: int, height: int, num_players: int) -> list[Position]:
    """P1 top-left, P2 bottom-right, P3 top-right, P4 bottom-left (2 players face each other)."""
    if not MIN_PLAYERS <= num_players <= MAX_PLAYERS:
        raise ValueError(f"Number of players must be {MIN_PLAYERS}-{MAX_PLAYERS}, got {num_players}")
    corners = [
        Position(0, 0),
        Position(width - 1, height - 1),
        Position(width - 1, 0),
        Position(0, height - 1),
    ]
    return corners[:num_players]


def spawn_zone(corner: Position, width: int, height: int, size: int) -> set[Position]:
    xs = range(0, size) if corner.x == 0 else range(width - size, width)
    ys = range(0, size) if corner.y == 0 else range(height - size, height)
    return {Position(x, y) for x in xs for y in ys}


def generate_board(config: GameConfig, num_players: int, rng: random.Random) -> Board:
    w, h = config.width, config.height
    terrain = [[TerrainType.GRASS] * w for _ in range(h)]
    elevation = [[0] * w for _ in range(h)]

    protected: set[Position] = set()
    for corner in corner_positions(w, h, MAX_PLAYERS):  # keep all corners fair, even unused ones
        protected |= spawn_zone(corner, w, h, config.spawn_zone_size)
    free = [Position(x, y) for y in range(h) for x in range(w) if Position(x, y) not in protected]

    def in_bounds(p: Position) -> bool:
        return 0 <= p.x < w and 0 <= p.y < h

    # Hills: a raised diamond (level 1) with a peak (level 1 or 2) in the middle.
    hills = max(1, w * h * config.hills_per_100_tiles // 100)
    for _ in range(hills):
        centre = rng.choice(free)
        peak = rng.randint(1, config.max_elevation)
        for p in (centre, *centre.neighbours()):
            if in_bounds(p):
                elevation[p.y][p.x] = max(elevation[p.y][p.x], 1)
        elevation[centre.y][centre.x] = max(elevation[centre.y][centre.x], peak)

    # Mud: short random walks.
    patches = max(1, w * h * config.mud_patches_per_100_tiles // 100)
    for _ in range(patches):
        p = rng.choice(free)
        for _ in range(rng.randint(3, 6)):
            terrain[p.y][p.x] = TerrainType.MUD
            step = rng.choice(p.neighbours())
            if in_bounds(step):
                p = step

    # Spawn zones: flat grass.
    for p in protected:
        terrain[p.y][p.x] = TerrainType.GRASS
        elevation[p.y][p.x] = 0

    for row in elevation:
        for x, value in enumerate(row):
            row[x] = max(0, min(config.max_elevation, value))

    if config.smooth_elevation:
        _smooth(elevation, w, h)

    return Board([[Tile(terrain[y][x], elevation[y][x]) for x in range(w)] for y in range(h)])


def _smooth(elevation: list[list[int]], w: int, h: int) -> None:
    """Lower tiles until no tile is more than 1 level above a neighbour."""
    changed = True
    while changed:
        changed = False
        for y in range(h):
            for x in range(w):
                for p in Position(x, y).neighbours():
                    if 0 <= p.x < w and 0 <= p.y < h and elevation[y][x] > elevation[p.y][p.x] + 1:
                        elevation[y][x] = elevation[p.y][p.x] + 1
                        changed = True


def spawn_positions(board: Board, corner: Position, count: int, taken: set[Position]) -> list[Position]:
    """The ``count`` free tiles closest to ``corner`` (deterministic order).

    Diagonal tiles are filled last at each distance, so the Boss is not boxed in on turn 1.
    """

    def key(p: Position) -> tuple[int, bool, int, int]:
        dx, dy = abs(p.x - corner.x), abs(p.y - corner.y)
        return (dx + dy, dx == dy, p.y, p.x)

    candidates = sorted((p for p in board.positions() if p not in taken), key=key)
    if len(candidates) < count:
        raise ValueError("Not enough free tiles to place units")
    return candidates[:count]
