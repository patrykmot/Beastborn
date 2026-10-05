"""Mouse + keyboard -> UI intents."""
from __future__ import annotations

import pygame

from beastborn.ui.interface import Cancel, ClickTile, EndTurn, HoverTile, InputAdapter, Quit, UIIntent
from beastborn.ui.pygame_ui.layout import Layout


class MouseKeyboardInput(InputAdapter):
    """LMB: select / move / attack. RMB or Esc: deselect. E / Space / button: end turn."""

    def __init__(self, layout: Layout):
        self.layout = layout

    def poll(self) -> list[UIIntent]:
        return [i for e in pygame.event.get() if (i := self.translate(e)) is not None]

    def translate(self, event: pygame.event.Event) -> UIIntent | None:
        if event.type == pygame.QUIT:
            return Quit()
        if event.type == pygame.MOUSEMOTION:
            return HoverTile(self.layout.tile_at(*event.pos))
        if event.type == pygame.MOUSEBUTTONDOWN:
            if event.button == 3:
                return Cancel()
            if event.button == 1:
                if self.layout.end_turn_button.collidepoint(event.pos):
                    return EndTurn()
                pos = self.layout.tile_at(*event.pos)
                return ClickTile(pos) if pos is not None else None
        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_e, pygame.K_SPACE):
                return EndTurn()
            if event.key == pygame.K_ESCAPE:
                return Cancel()
        return None
