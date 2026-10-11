"""Terrain types and tiles.

Only *names* live here. How a terrain affects movement cost or defence is a rule,
so the numbers live in ``beastborn/constant.py``.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TerrainType(Enum):
    GRASS = "grass"  # normal terrain
    MUD = "mud"  # difficult terrain


@dataclass(frozen=True)
class Tile:
    terrain: TerrainType = TerrainType.GRASS
    elevation: int = 0
