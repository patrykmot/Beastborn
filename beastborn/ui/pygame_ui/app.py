"""Pygame desktop frontend."""
from __future__ import annotations

import pygame

from beastborn.control.controller import PlayerController
from beastborn.game.state_machine import GameStateMachine
from beastborn.ui.interaction import Interaction
from beastborn.ui.interface import Frontend, InputAdapter
from beastborn.ui.pygame_ui import theme
from beastborn.ui.pygame_ui.input_mouse_kb import MouseKeyboardInput
from beastborn.ui.pygame_ui.layout import Layout
from beastborn.ui.pygame_ui.renderer import Renderer


class PygameFrontend(Frontend):
    def __init__(self, input_adapter_factory=MouseKeyboardInput):
        self._input_factory = input_adapter_factory

    def run(
        self,
        gsm: GameStateMachine,
        controllers: dict[int, PlayerController] | None = None,
        max_frames: int | None = None,
        screenshot: str | None = None,
    ) -> Interaction:
        """Main loop. ``max_frames`` / ``screenshot`` are for tests and docs."""
        pygame.init()
        try:
            view = gsm.view()
            layout = Layout.for_board(view.board.width, view.board.height)
            screen = pygame.display.set_mode((layout.width, layout.height))
            pygame.display.set_caption("Beastborn")
            clock = pygame.time.Clock()
            renderer = Renderer(layout)
            input_adapter: InputAdapter = self._input_factory(layout)
            ui = Interaction(gsm, controllers)

            frame = 0
            while not ui.quit_requested:
                for intent in input_adapter.poll():
                    ui.handle(intent)
                ui.tick()
                renderer.draw(screen, ui)
                pygame.display.flip()
                clock.tick(theme.FPS)
                frame += 1
                if max_frames is not None and frame >= max_frames:
                    break
            if screenshot:
                pygame.image.save(screen, screenshot)
            return ui
        finally:
            pygame.quit()
