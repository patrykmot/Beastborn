"""Frontend-neutral UI contracts.

A frontend (pygame, console, web...) renders a ``GameView`` and turns device input into
``UIIntent``s. The ``Interaction`` presenter (ui/interaction.py) turns intents into GSM
commands, so input devices and renderers can be swapped independently.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from beastborn.domain.position import Position


# ------------------------------------------------------------------ intents
@dataclass(frozen=True)
class ClickTile:
    position: Position


@dataclass(frozen=True)
class HoverTile:
    position: Position | None


@dataclass(frozen=True)
class EndTurn:
    pass


@dataclass(frozen=True)
class Cancel:
    pass


@dataclass(frozen=True)
class Quit:
    pass


UIIntent = ClickTile | HoverTile | EndTurn | Cancel | Quit


# ------------------------------------------------------------------ contracts
class InputAdapter(ABC):
    """Translates raw device events (mouse, keyboard, touch...) into intents."""

    @abstractmethod
    def poll(self) -> list[UIIntent]: ...


class Frontend(ABC):
    """Runs the game loop for one kind of display."""

    @abstractmethod
    def run(self, gsm, controllers=None) -> None: ...
