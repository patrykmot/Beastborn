"""Request bodies (validated by pydantic)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from beastborn.constant import (
    BOT_PLAYERS, DEFAULT_MAP_SIZE, DEFAULT_PLAYERS, MAX_BOTS, MAX_PLAYERS, MAX_SEED, MIN_PLAYERS, MapSize,
)

IntentType = Literal["click", "end_turn"]


class NewGameRequest(BaseModel):
    players: int = Field(DEFAULT_PLAYERS, ge=MIN_PLAYERS, le=MAX_PLAYERS)
    seed: int | None = Field(None, ge=0, le=MAX_SEED)
    size: MapSize = DEFAULT_MAP_SIZE
    bots: list[int] = Field(default_factory=list)  # seats (player indices) played by the bot; none by default

    @model_validator(mode="after")
    def check_bots(self) -> NewGameRequest:
        if not self.bots:
            return self
        if len(self.bots) > MAX_BOTS:
            raise ValueError(f"At most {MAX_BOTS} bot per game")
        if self.players != BOT_PLAYERS:
            raise ValueError(f"A bot can only play in a {BOT_PLAYERS}-player game")
        if len(set(self.bots)) != len(self.bots) or not all(0 <= seat < self.players for seat in self.bots):
            raise ValueError(f"Bot seats must be different player numbers from 0 to {self.players - 1}")
        return self


class IntentRequest(BaseModel):
    type: IntentType
    x: int | None = Field(None, ge=0)
    y: int | None = Field(None, ge=0)
