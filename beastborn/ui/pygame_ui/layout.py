"""Screen geometry: maps pixels to tiles and back."""
from __future__ import annotations

from dataclasses import dataclass

import pygame

from beastborn.domain.position import Position
from beastborn.ui.pygame_ui import theme


@dataclass
class Layout:
    tile: int
    board_w: int
    board_h: int
    width: int
    height: int
    panel: pygame.Rect
    end_turn_button: pygame.Rect

    @classmethod
    def for_board(cls, columns: int, rows: int) -> Layout:
        tile = max(theme.MIN_TILE, min(theme.MAX_TILE, theme.MAX_BOARD_PIXELS // max(columns, rows)))
        board_w, board_h = columns * tile, rows * tile
        height = max(board_h, theme.MIN_HEIGHT)
        width = board_w + theme.PANEL_WIDTH
        panel = pygame.Rect(board_w, 0, theme.PANEL_WIDTH, height)
        button = pygame.Rect(panel.x + 16, height - 56, panel.width - 32, 40)
        return cls(tile, board_w, board_h, width, height, panel, button)

    def tile_rect(self, pos: Position) -> pygame.Rect:
        return pygame.Rect(pos.x * self.tile, pos.y * self.tile, self.tile, self.tile)

    def tile_center(self, pos: Position) -> tuple[int, int]:
        return self.tile_rect(pos).center

    def tile_at(self, px: int, py: int) -> Position | None:
        if 0 <= px < self.board_w and 0 <= py < self.board_h:
            return Position(px // self.tile, py // self.tile)
        return None
