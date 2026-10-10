"""Request bodies (validated by pydantic)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from beastborn.constance import DEFAULT_MAP_SIZE, DEFAULT_PLAYERS, MAX_PLAYERS, MAX_SEED, MIN_PLAYERS, MapSize

IntentType = Literal["click", "end_turn", "cancel"]


class NewGameRequest(BaseModel):
    players: int = Field(DEFAULT_PLAYERS, ge=MIN_PLAYERS, le=MAX_PLAYERS)
    seed: int | None = Field(None, ge=0, le=MAX_SEED)
    size: MapSize = DEFAULT_MAP_SIZE


class IntentRequest(BaseModel):
    type: IntentType
    x: int | None = Field(None, ge=0)
    y: int | None = Field(None, ge=0)
