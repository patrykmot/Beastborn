"""Request bodies (validated by pydantic)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

MAP_SIZES = (10, 12, 16)


class NewGameRequest(BaseModel):
    players: int = Field(2, ge=2, le=4)
    seed: int | None = Field(None, ge=0, le=2**31 - 1)
    size: Literal[10, 12, 16] = 12


class IntentRequest(BaseModel):
    type: Literal["click", "end_turn", "cancel"]
    x: int | None = Field(None, ge=0)
    y: int | None = Field(None, ge=0)
