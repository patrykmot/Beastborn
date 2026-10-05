"""Grid coordinates."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class Position:
    """A tile coordinate. ``x`` is the column, ``y`` is the row (0,0 = top-left)."""

    x: int
    y: int

    def neighbours(self) -> tuple[Position, ...]:
        """4-directional neighbours (may be outside the board - check with Board.in_bounds)."""
        return (
            Position(self.x, self.y - 1),
            Position(self.x + 1, self.y),
            Position(self.x, self.y + 1),
            Position(self.x - 1, self.y),
        )

    def manhattan(self, other: Position) -> int:
        return abs(self.x - other.x) + abs(self.y - other.y)

    def __str__(self) -> str:
        return f"({self.x},{self.y})"
