"""Random (seeded) battlefield generation.

RNG is allowed here: it only shapes the map. Combat itself has no randomness.
"""
from __future__ import annotations

import random

from beastborn import constant as C
from beastborn.domain.board import Board
from beastborn.domain.position import Position
from beastborn.domain.terrain import TerrainType, Tile

Grid = list[list[int]]


def validate_player_count(num_players: int) -> None:
    if not C.MIN_PLAYERS <= num_players <= C.MAX_PLAYERS:
        raise ValueError(f"Number of players must be {C.MIN_PLAYERS}-{C.MAX_PLAYERS}, got {num_players}")


def corner_positions(width: int, height: int, num_players: int) -> list[Position]:
    """P1 top-left, P2 bottom-right, P3 top-right, P4 bottom-left (2 players face each other)."""
    validate_player_count(num_players)
    corners: list[Position] = [
        Position(0, 0),
        Position(width - 1, height - 1),
        Position(width - 1, 0),
        Position(0, height - 1),
    ]
    return corners[:num_players]


def spawn_zone(corner: Position, width: int, height: int, size: int) -> set[Position]:
    xs: range = range(0, size) if corner.x == 0 else range(width - size, width)
    ys: range = range(0, size) if corner.y == 0 else range(height - size, height)
    return {Position(x, y) for x in xs for y in ys}


def validate_map_size(width: int, height: int) -> None:
    if width < 2 * C.SPAWN_ZONE_SIZE or height < 2 * C.SPAWN_ZONE_SIZE:
        raise ValueError("Map too small for the spawn zones")
    if C.UNITS_PER_PLAYER + 1 > C.SPAWN_ZONE_SIZE**2:
        raise ValueError("Boss + units must fit in the spawn zone")


def _density_count(width: int, height: int, per_100_tiles: int, minimum: int) -> int:
    """How many features (hills, mud patches) a map of this size gets."""
    return max(minimum, width * height * per_100_tiles // C.TILES_PER_DENSITY_UNIT)


def generate_board(width: int, height: int, num_players: int, rng: random.Random) -> Board:
    validate_map_size(width, height)
    w, h = width, height
    terrain: list[list[TerrainType]] = [[TerrainType.GRASS] * w for _ in range(h)]
    elevation: Grid = [[0] * w for _ in range(h)]

    protected: set[Position] = set()
    for corner in corner_positions(w, h, C.MAX_PLAYERS):  # keep all corners fair, even unused ones
        protected |= spawn_zone(corner, w, h, C.SPAWN_ZONE_SIZE)
    free: list[Position] = [Position(x, y) for y in range(h) for x in range(w) if Position(x, y) not in protected]

    # Hills: a raised diamond (base level) with a peak (base level .. MAX_ELEVATION) in the middle.
    for _ in range(_density_count(w, h, C.HILLS_PER_100_TILES, C.MIN_HILLS)):
        centre: Position = rng.choice(free)
        peak: int = rng.randint(C.HILL_BASE_ELEVATION, C.MAX_ELEVATION)
        for p in (centre, *centre.neighbours()):
            if p.inside(w, h):
                elevation[p.y][p.x] = max(elevation[p.y][p.x], C.HILL_BASE_ELEVATION)
        elevation[centre.y][centre.x] = max(elevation[centre.y][centre.x], peak)

    # Mud: short random walks.
    for _ in range(_density_count(w, h, C.MUD_PATCHES_PER_100_TILES, C.MIN_MUD_PATCHES)):
        pos: Position = rng.choice(free)
        for _ in range(rng.randint(C.MUD_WALK_MIN_STEPS, C.MUD_WALK_MAX_STEPS)):
            terrain[pos.y][pos.x] = TerrainType.MUD
            step: Position = rng.choice(pos.neighbours())
            if step.inside(w, h):
                pos = step

    # Spawn zones: flat grass.
    for p in protected:
        terrain[p.y][p.x] = TerrainType.GRASS
        elevation[p.y][p.x] = 0

    for row in elevation:
        for x, value in enumerate(row):
            row[x] = max(0, min(C.MAX_ELEVATION, value))

    if C.SMOOTH_ELEVATION:
        _smooth(elevation, w, h)

    return Board([[Tile(terrain[y][x], elevation[y][x]) for x in range(w)] for y in range(h)])


def _smooth(elevation: Grid, w: int, h: int) -> None:
    """Lower tiles until no tile is more than 1 level above a neighbour."""
    changed: bool = True
    while changed:
        changed = False
        for y in range(h):
            for x in range(w):
                for p in Position(x, y).neighbours():
                    if p.inside(w, h) and elevation[y][x] > elevation[p.y][p.x] + 1:
                        elevation[y][x] = elevation[p.y][p.x] + 1
                        changed = True


def spawn_positions(board: Board, corner: Position, count: int, taken: set[Position]) -> list[Position]:
    """The ``count`` free tiles closest to ``corner`` (deterministic order).

    Diagonal tiles are filled last at each distance, so the Boss is not boxed in on turn 1.
    """

    def key(p: Position) -> tuple[int, bool, int, int]:
        dx: int = abs(p.x - corner.x)
        dy: int = abs(p.y - corner.y)
        return (dx + dy, dx == dy, p.y, p.x)

    candidates: list[Position] = sorted((p for p in board.positions() if p not in taken), key=key)
    if len(candidates) < count:
        raise ValueError("Not enough free tiles to place units")
    return candidates[:count]
