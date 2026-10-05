"""The battlefield grid. Terrain never changes after generation."""
from __future__ import annotations

from collections.abc import Iterator, Sequence

from beastborn.domain.position import Position
from beastborn.domain.terrain import TerrainType, Tile


class Board:
    def __init__(self, tiles: Sequence[Sequence[Tile]]):
        if not tiles or not tiles[0]:
            raise ValueError("Board needs at least one tile")
        width = len(tiles[0])
        if any(len(row) != width for row in tiles):
            raise ValueError("All board rows must have the same width")
        # stored as rows: self._tiles[y][x]
        self._tiles: tuple[tuple[Tile, ...], ...] = tuple(tuple(row) for row in tiles)
        self.width = width
        self.height = len(tiles)

    @classmethod
    def filled(cls, width: int, height: int, tile: Tile = Tile()) -> Board:
        return cls([[tile] * width for _ in range(height)])

    @classmethod
    def from_strings(cls, terrain_rows: Sequence[str], elevation_rows: Sequence[str] | None = None) -> Board:
        """Build a board from text: ``.`` = grass, ``m`` = mud; elevation rows are digits.

        Handy for tests and hand-made maps.
        """
        symbols = {".": TerrainType.GRASS, "m": TerrainType.MUD}
        rows = []
        for y, line in enumerate(terrain_rows):
            row = []
            for x, ch in enumerate(line):
                elevation = int(elevation_rows[y][x]) if elevation_rows else 0
                row.append(Tile(symbols[ch], elevation))
            rows.append(row)
        return cls(rows)

    def in_bounds(self, pos: Position) -> bool:
        return 0 <= pos.x < self.width and 0 <= pos.y < self.height

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
        lines = []
        for row in self._tiles:
            lines.append(" ".join(("m" if t.terrain is TerrainType.MUD else ".") + str(t.elevation) for t in row))
        return "\n".join(lines)
