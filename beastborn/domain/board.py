"""The battlefield grid. Terrain never changes after generation."""
from __future__ import annotations

from collections.abc import Iterator, Sequence

from beastborn.constance import TERRAIN_SYMBOLS
from beastborn.domain.position import Position
from beastborn.domain.terrain import TerrainType, Tile

_TERRAIN_BY_SYMBOL: dict[str, TerrainType] = {symbol: TerrainType(value) for symbol, value in TERRAIN_SYMBOLS.items()}
_SYMBOL_BY_TERRAIN: dict[TerrainType, str] = {terrain: symbol for symbol, terrain in _TERRAIN_BY_SYMBOL.items()}


class Board:
    def __init__(self, tiles: Sequence[Sequence[Tile]]):
        if not tiles or not tiles[0]:
            raise ValueError("Board needs at least one tile")
        width: int = len(tiles[0])
        if any(len(row) != width for row in tiles):
            raise ValueError("All board rows must have the same width")
        # stored as rows: self._tiles[y][x]
        self._tiles: tuple[tuple[Tile, ...], ...] = tuple(tuple(row) for row in tiles)
        self.width: int = width
        self.height: int = len(tiles)

    @classmethod
    def filled(cls, width: int, height: int, tile: Tile = Tile()) -> Board:
        return cls([[tile] * width for _ in range(height)])

    @classmethod
    def from_strings(cls, terrain_rows: Sequence[str], elevation_rows: Sequence[str] | None = None) -> Board:
        """Build a board from text: ``.`` = grass, ``m`` = mud; elevation rows are digits.

        Handy for tests and hand-made maps.
        """
        rows: list[list[Tile]] = [
            [
                Tile(_TERRAIN_BY_SYMBOL[symbol], int(elevation_rows[y][x]) if elevation_rows else 0)
                for x, symbol in enumerate(line)
            ]
            for y, line in enumerate(terrain_rows)
        ]
        return cls(rows)

    def in_bounds(self, pos: Position) -> bool:
        return pos.inside(self.width, self.height)

    def tile(self, pos: Position) -> Tile:
        if not self.in_bounds(pos):
            raise IndexError(f"Position {pos} is outside the {self.width}x{self.height} board")
        return self._tiles[pos.y][pos.x]

    def positions(self) -> Iterator[Position]:
        for y in range(self.height):
            for x in range(self.width):
                yield Position(x, y)

    def neighbours(self, pos: Position) -> list[Position]:
        return [p for p in pos.neighbours() if self.in_bounds(p)]

    def __str__(self) -> str:
        return "\n".join(
            " ".join(f"{_SYMBOL_BY_TERRAIN[tile.terrain]}{tile.elevation}" for tile in row) for row in self._tiles
        )
