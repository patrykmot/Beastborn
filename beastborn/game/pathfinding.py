"""Cheapest paths using the Calculation Engine's step costs (Dijkstra)."""
from __future__ import annotations

import heapq
from dataclasses import dataclass

from beastborn.domain.board import Board
from beastborn.domain.position import Position
from beastborn.domain.terrain import Tile
from beastborn.domain.unit import UnitStats
from beastborn.engine.interface import CalculationEngine


@dataclass(frozen=True)
class PathInfo:
    cost: int
    path: tuple[Position, ...]  # from start to destination, inclusive


def reachable(
    board: Board,
    engine: CalculationEngine,
    unit: UnitStats,
    start: Position,
    budget: int,
    blocked: set[Position],
) -> dict[Position, PathInfo]:
    """All tiles the unit can end its move on with ``budget`` energy (start tile excluded).

    Units cannot pass through or stop on ``blocked`` tiles (D15).
    """
    best: dict[Position, int] = {start: 0}
    parent: dict[Position, Position] = {}
    queue: list[tuple[int, int, int, Position]] = [(0, start.y, start.x, start)]
    while queue:
        cost, _, _, pos = heapq.heappop(queue)
        if cost > best.get(pos, cost):
            continue
        src_tile: Tile = board.tile(pos)
        for nxt in board.neighbours(pos):
            if nxt in blocked:
                continue
            new_cost: int = cost + engine.step_cost(unit, src_tile, board.tile(nxt))
            if new_cost <= budget and new_cost < best.get(nxt, budget + 1):
                best[nxt] = new_cost
                parent[nxt] = pos
                heapq.heappush(queue, (new_cost, nxt.y, nxt.x, nxt))

    result: dict[Position, PathInfo] = {}
    for pos, cost in best.items():
        if pos == start:
            continue
        path: list[Position] = [pos]
        while path[-1] != start:
            path.append(parent[path[-1]])
        result[pos] = PathInfo(cost, tuple(reversed(path)))
    return result
