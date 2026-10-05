"""Colours and sizes for the pygame frontend."""
from beastborn.domain.terrain import TerrainType

BACKGROUND = (24, 26, 32)
PANEL = (34, 37, 46)
PANEL_LINE = (60, 64, 78)
TEXT = (225, 228, 235)
TEXT_DIM = (150, 155, 170)
TEXT_ERROR = (255, 120, 110)
GRID = (30, 34, 30)

# index = elevation level
TERRAIN_COLORS = {
    TerrainType.GRASS: [(92, 150, 78), (128, 176, 96), (170, 200, 128)],
    TerrainType.MUD: [(104, 80, 54), (132, 104, 70), (160, 128, 88)],
}
ELEVATION_TEXT = (40, 50, 35)

PLAYER_COLORS = [(214, 72, 64), (62, 124, 222), (226, 184, 48), (160, 86, 204)]
BOSS_RING = (250, 220, 120)

SELECTED = (255, 236, 90)
REACHABLE_OVERLAY = (255, 255, 255, 70)
PATH = (255, 236, 90)
TARGET = (255, 70, 60)
HP_BAR = (90, 210, 100)
HP_BACK = (110, 30, 30)
EN_BAR = (90, 170, 255)
EN_BACK = (30, 40, 70)
VENOM = (120, 230, 60)

BUTTON = (70, 120, 90)
BUTTON_HOVER = (90, 150, 112)
BUTTON_DISABLED = (70, 72, 80)

PANEL_WIDTH = 380
MAX_BOARD_PIXELS = 660
MIN_TILE, MAX_TILE = 32, 64
MIN_HEIGHT = 660
FPS = 30
