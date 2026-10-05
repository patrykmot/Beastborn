"""Players."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Player:
    index: int
    name: str
    eliminated: bool = False
