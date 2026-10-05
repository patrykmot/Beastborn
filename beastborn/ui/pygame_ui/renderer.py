"""Draws the board, units, highlights and the side panel."""
from __future__ import annotations

import pygame

from beastborn.domain.effects import EffectKind
from beastborn.domain.terrain import TerrainType
from beastborn.game.state import Phase
from beastborn.game.view import UnitView
from beastborn.ui.interaction import Interaction
from beastborn.ui.pygame_ui import theme
from beastborn.ui.pygame_ui.layout import Layout


class Renderer:
    def __init__(self, layout: Layout):
        self.layout = layout
        self.font = pygame.font.Font(None, 22)
        self.font_small = pygame.font.Font(None, 18)
        self.font_title = pygame.font.Font(None, 30)
        self.font_big = pygame.font.Font(None, 56)
        self.font_unit = pygame.font.Font(None, max(18, layout.tile // 2))
        self._overlay = pygame.Surface((layout.tile, layout.tile), pygame.SRCALPHA)
        self._overlay.fill(theme.REACHABLE_OVERLAY)

    # ------------------------------------------------------------------ entry point
    def draw(self, screen: pygame.Surface, ui: Interaction) -> None:
        screen.fill(theme.BACKGROUND)
        self._draw_board(screen, ui)
        self._draw_highlights(screen, ui)
        for unit in ui.view.units:
            self._draw_unit(screen, unit, ui)
        self._draw_panel(screen, ui)
        if ui.view.phase is Phase.GAME_OVER:
            self._draw_game_over(screen, ui)

    # ------------------------------------------------------------------ board
    def _draw_board(self, screen: pygame.Surface, ui: Interaction) -> None:
        board = ui.view.board
        for pos in board.positions():
            tile = board.tile(pos)
            rect = self.layout.tile_rect(pos)
            shades = theme.TERRAIN_COLORS[tile.terrain]
            pygame.draw.rect(screen, shades[min(tile.elevation, len(shades) - 1)], rect)
            if tile.terrain is TerrainType.MUD:  # a few dots so mud reads even without colour
                for dx, dy in ((0.25, 0.3), (0.7, 0.55), (0.4, 0.8)):
                    centre = (rect.x + int(rect.w * dx), rect.y + int(rect.h * dy))
                    pygame.draw.circle(screen, (80, 60, 40), centre, 2)
            for level in range(tile.elevation):  # small triangles = elevation
                x = rect.x + 4 + level * 10
                pygame.draw.polygon(
                    screen, theme.ELEVATION_TEXT, [(x, rect.y + 12), (x + 4, rect.y + 4), (x + 8, rect.y + 12)]
                )
            pygame.draw.rect(screen, theme.GRID, rect, 1)

    def _draw_highlights(self, screen: pygame.Surface, ui: Interaction) -> None:
        for pos, info in ui.reachable.items():
            rect = self.layout.tile_rect(pos)
            screen.blit(self._overlay, rect)
            cost = self.font_small.render(str(info.cost), True, (20, 20, 20))
            screen.blit(cost, cost.get_rect(bottomright=(rect.right - 3, rect.bottom - 2)))

        path = ui.hover_path()
        if path is not None and len(path.path) > 1:
            points = [self.layout.tile_center(p) for p in path.path]
            pygame.draw.lines(screen, theme.PATH, False, points, 3)

        view = ui.view
        for target_id in ui.targets:
            target = view.unit(target_id)
            if target is not None:
                pygame.draw.rect(screen, theme.TARGET, self.layout.tile_rect(target.position).inflate(-2, -2), 3)

        selected = ui.selected_unit
        if selected is not None:
            pygame.draw.rect(screen, theme.SELECTED, self.layout.tile_rect(selected.position), 3)

    # ------------------------------------------------------------------ units
    def _draw_unit(self, screen: pygame.Surface, unit: UnitView, ui: Interaction) -> None:
        rect = self.layout.tile_rect(unit.position)
        t = self.layout.tile
        cx, cy = rect.centerx, rect.y + int(t * 0.44)
        radius = int(t * (0.36 if unit.is_boss else 0.30))
        color = theme.PLAYER_COLORS[unit.owner % len(theme.PLAYER_COLORS)]
        if unit.is_boss:
            pygame.draw.circle(screen, theme.BOSS_RING, (cx, cy), radius + 3)
        pygame.draw.circle(screen, color, (cx, cy), radius)
        pygame.draw.circle(screen, (20, 20, 20), (cx, cy), radius, 1)
        letter = self.font_unit.render(unit.stats.code, True, (255, 255, 255))
        screen.blit(letter, letter.get_rect(center=(cx, cy)))

        bar_w, x = t - 10, rect.x + 5
        self._bar(screen, x, rect.bottom - 11, bar_w, 4, unit.hp / unit.stats.hp, theme.HP_BAR, theme.HP_BACK)
        self._bar(screen, x, rect.bottom - 6, bar_w, 3, unit.energy / unit.stats.max_en, theme.EN_BAR, theme.EN_BACK)

        if any(e.kind is EffectKind.VENOM for e in unit.effects):
            pygame.draw.circle(screen, theme.VENOM, (rect.right - 7, rect.y + 7), 4)

        # Hovering an attackable enemy: show the exact damage over it.
        if unit.id in ui.targets and ui.hovered == unit.position:
            dmg = self.font.render(f"-{ui.targets[unit.id].damage}", True, (255, 255, 255))
            box = dmg.get_rect(midbottom=(cx, rect.y + 2)).inflate(8, 4)
            pygame.draw.rect(screen, theme.TARGET, box, border_radius=4)
            screen.blit(dmg, dmg.get_rect(center=box.center))

    @staticmethod
    def _bar(screen, x, y, w, h, ratio, fg, bg) -> None:
        pygame.draw.rect(screen, bg, (x, y, w, h))
        pygame.draw.rect(screen, fg, (x, y, int(w * max(0.0, min(1.0, ratio))), h))

    # ------------------------------------------------------------------ side panel
    def _draw_panel(self, screen: pygame.Surface, ui: Interaction) -> None:
        panel = self.layout.panel
        pygame.draw.rect(screen, theme.PANEL, panel)
        pygame.draw.line(screen, theme.PANEL_LINE, panel.topleft, panel.bottomleft, 2)
        view = ui.view
        x = panel.x + 16
        y = 14

        y = self._text(screen, "BEASTBORN", x, y, self.font_title) + 4
        active = view.players[view.active_player]
        color = theme.PLAYER_COLORS[active.index % len(theme.PLAYER_COLORS)]
        pygame.draw.rect(screen, color, (x, y + 2, 14, 14))
        y = self._text(screen, f"Round {view.round} - {active.name}'s turn", x + 22, y)

        for player in view.players:
            alive = len(view.units_of(player.index))
            status = "eliminated" if player.eliminated else f"{alive} units"
            pc = theme.PLAYER_COLORS[player.index % len(theme.PLAYER_COLORS)]
            pygame.draw.circle(screen, pc, (x + 6, y + 8), 5)
            y = self._text(screen, f"{player.name}: {status}", x + 18, y, self.font_small, theme.TEXT_DIM)
        y += 6
        y = self._separator(screen, y)

        selected = ui.selected_unit
        hovered = ui.hovered_unit
        if selected is not None:
            y = self._unit_info(screen, selected, x, y, "Selected")
        if hovered is not None and hovered is not selected and (selected is None or hovered.id != selected.id):
            y = self._unit_info(screen, hovered, x, y, "Hover")
        if selected is None and hovered is None:
            y = self._text(screen, "Click one of your units.", x, y, colour=theme.TEXT_DIM)

        y = self._hover_details(screen, ui, x, y)

        if ui.message:
            colour = theme.TEXT_ERROR if ui.message_is_error else theme.TEXT
            y = self._text(screen, ui.message, x, y + 4, colour=colour)

        self._draw_log(screen, ui, x)
        self._draw_button(screen, ui)

    def _unit_info(self, screen, unit: UnitView, x: int, y: int, title: str) -> int:
        owner = f"P{unit.owner + 1}"
        y = self._text(screen, f"{title}: {unit.name} ({owner})", x, y)
        s = unit.stats
        def_text = f"DEF {unit.current_defense}" + (f" (base {unit.defense})" if unit.current_defense != unit.defense else "")
        lines = [
            f"HP {unit.hp}/{s.hp}    EN {unit.energy}/{s.max_en} (+{s.reg_en}/turn)",
            f"ATK {s.atk} x EN_ATK {s.en_atk} = {s.atk * s.en_atk}    {def_text}",
        ]
        effects = [f"{e.kind.value} {e.magnitude} ({e.remaining_turns}t)" for e in unit.effects]
        on_hit = [f"{e.kind.value}" for e in s.on_hit]
        if on_hit:
            lines.append("On hit: " + ", ".join(on_hit))
        if effects:
            lines.append("Affected by: " + ", ".join(effects))
        for line in lines:
            y = self._text(screen, line, x + 8, y, self.font_small, theme.TEXT_DIM)
        return y + 6

    def _hover_details(self, screen, ui: Interaction, x: int, y: int) -> int:
        if ui.hovered is None:
            return y
        tile = ui.view.board.tile(ui.hovered)
        y = self._text(
            screen, f"Tile {ui.hovered}: {tile.terrain.value}, elevation {tile.elevation}",
            x, y, self.font_small, theme.TEXT_DIM,
        )
        path = ui.hover_path()
        if path is not None:
            y = self._text(screen, f"Move cost: {path.cost} EN", x, y, self.font_small)
        attack = ui.hover_attack()
        if attack is not None:
            target = ui.hovered_unit
            in_range = target is not None and target.id in ui.targets
            note = "" if in_range else "  (cannot attack now)"
            y = self._text(screen, f"Attack: {attack.formula()}{note}", x, y, self.font_small)
            if target is not None:
                y = self._text(
                    screen, f"Damage {attack.damage} -> target HP {max(0, target.hp - attack.damage)}",
                    x, y, self.font_small,
                )
        return y

    def _draw_log(self, screen, ui: Interaction, x: int) -> None:
        bottom = self.layout.end_turn_button.y - 34
        lines = list(ui.log)[-9:]
        y = bottom - len(lines) * 18
        self._separator(screen, y - 8)
        for line in lines:
            self._text(screen, self._fit(line, self.layout.panel.width - 32), x, y, self.font_small, theme.TEXT_DIM)
            y += 18
        self._text(screen, "LMB select/move/attack  RMB/Esc deselect  E end turn",
                   x, bottom + 6, self.font_small, theme.TEXT_DIM)

    def _draw_button(self, screen, ui: Interaction) -> None:
        rect = self.layout.end_turn_button
        enabled = ui.view.phase is not Phase.GAME_OVER and ui.human_turn
        hover = rect.collidepoint(pygame.mouse.get_pos()) if pygame.display.get_init() else False
        colour = theme.BUTTON_DISABLED if not enabled else (theme.BUTTON_HOVER if hover else theme.BUTTON)
        pygame.draw.rect(screen, colour, rect, border_radius=6)
        label = self.font.render("End turn (E)", True, theme.TEXT)
        screen.blit(label, label.get_rect(center=rect.center))

    def _draw_game_over(self, screen, ui: Interaction) -> None:
        shade = pygame.Surface((self.layout.board_w, self.layout.board_h), pygame.SRCALPHA)
        shade.fill((0, 0, 0, 150))
        screen.blit(shade, (0, 0))
        winner = ui.view.winner
        text = "Draw" if winner is None else f"{ui.view.players[winner].name} wins!"
        label = self.font_big.render(text, True, (255, 255, 255))
        screen.blit(label, label.get_rect(center=(self.layout.board_w // 2, self.layout.board_h // 2)))

    # ------------------------------------------------------------------ helpers
    def _text(self, screen, text: str, x: int, y: int, font=None, colour=theme.TEXT) -> int:
        font = font or self.font
        surface = font.render(text, True, colour)
        screen.blit(surface, (x, y))
        return y + surface.get_height() + 3

    def _separator(self, screen, y: int) -> int:
        panel = self.layout.panel
        pygame.draw.line(screen, theme.PANEL_LINE, (panel.x + 12, y), (panel.right - 12, y))
        return y + 8

    def _fit(self, text: str, width: int) -> str:
        if self.font_small.size(text)[0] <= width:
            return text
        while text and self.font_small.size(text + "...")[0] > width:
            text = text[:-1]
        return text + "..."
