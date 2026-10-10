"""Every constant of the Beastborn package, in one place.

This module is a leaf: it imports nothing from ``beastborn``, so any layer (domain, engine,
game, control, ui) may import it without creating an import cycle or breaking the layering
rules checked by tests/test_architecture.py.

Rules numbers here are only the *defaults*. At runtime the game reads them through
``engine.rules_config.RulesConfig`` and ``game.config.GameConfig``, so a game can still be
started with different values (``RulesConfig(allow_block=True)``, ``GameConfig(width=16)``...).

The deployment scripts (beast_install.py, gercio_eu_pythonanywhere_com_wsgi.py) keep their own
constants: they run on the server before this package is on ``sys.path``.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Final, Literal, get_args

# ====================================================================== application
GAME_NAME: Final[str] = "Beastborn"
GAME_VERSION: Final[str] = "0.2"
GAME_TITLE: Final[str] = f"{GAME_NAME} v{GAME_VERSION}"  # shown in the browser and the server console

# ====================================================================== paths
PACKAGE_DIR: Final[Path] = Path(__file__).resolve().parent
PROJECT_DIR: Final[Path] = PACKAGE_DIR.parent
DEFAULT_UNITS_FILE: Final[Path] = PACKAGE_DIR / "data" / "units.json"
STATIC_DIR: Final[Path] = PACKAGE_DIR / "ui" / "web" / "static"

# ====================================================================== board / terrain
# Text symbol -> TerrainType value, used by Board.from_strings() and str(board).
TERRAIN_SYMBOLS: Final[Mapping[str, str]] = MappingProxyType({".": "grass", "m": "mud"})

# ====================================================================== players
MIN_PLAYERS: Final[int] = 2
MAX_PLAYERS: Final[int] = 4
DEFAULT_PLAYERS: Final[int] = 2
PLAYER_NAME_TEMPLATE: Final[str] = "Player {number}"  # number = index + 1

# ====================================================================== game setup (GameConfig defaults)
MapSize = Literal[10, 12, 16]
MAP_SIZES: Final[tuple[int, ...]] = get_args(MapSize)
DEFAULT_MAP_SIZE: Final[MapSize] = 12
DEFAULT_UNITS_PER_PLAYER: Final[int] = 4  # random recruits next to each Boss
DEFAULT_MAX_ELEVATION: Final[int] = 2
DEFAULT_SMOOTH_ELEVATION: Final[bool] = True  # neighbouring tiles differ by at most 1 level (D8)
DEFAULT_HILLS_PER_100_TILES: Final[int] = 4
DEFAULT_MUD_PATCHES_PER_100_TILES: Final[int] = 4
DEFAULT_SPAWN_ZONE_SIZE: Final[int] = 3  # flat grass square in each starting corner
DEFAULT_MAX_ATTACKS_PER_TURN: Final[int | None] = None  # None = limited only by energy (D3)

# ====================================================================== seeds
SEED_RANGE: Final[int] = 1_000_000  # random seeds are drawn from [0, SEED_RANGE)
MAX_SEED: Final[int] = 2**31 - 1  # largest seed accepted from the web client

# ====================================================================== map generator
HILL_BASE_ELEVATION: Final[int] = 1  # the diamond around a hill's peak
MIN_HILLS: Final[int] = 1
MIN_MUD_PATCHES: Final[int] = 1
MUD_WALK_MIN_STEPS: Final[int] = 3
MUD_WALK_MAX_STEPS: Final[int] = 6
TILES_PER_DENSITY_UNIT: Final[int] = 100  # "hills_per_100_tiles" etc.

# ====================================================================== unit data defaults (units.json)
DEFAULT_ATTACK_RANGE: Final[int] = 1
DEFAULT_MOVE_PENALTY: Final[int] = 0
MELEE_RANGE: Final[int] = 1  # attack_range > MELEE_RANGE = ranged unit

# ====================================================================== rules (RulesConfig defaults)
# Movement: energy per tile; a unit's move_penalty is added to every step.
EN_NT: Final[int] = 2  # normal terrain
EN_DT: Final[int] = 3  # difficult terrain
EN_MU: Final[int] = 2  # extra per elevation level gained (uphill)
EN_MD_NORMAL: Final[int] = 2  # moving downhill into normal terrain
EN_MD_DIFFICULT: Final[int] = 5  # moving downhill into difficult terrain (2 + 3 penalty)

# Combat (docs/game_mechanics.md, section numbers in brackets).
MOMENTUM_PER_LEVEL_NUMERATOR: Final[int] = 1  # [2] Momentum = ATK + 1/2 * ATK * Elevation_Modifier
MOMENTUM_PER_LEVEL_DENOMINATOR: Final[int] = 2
# [3] RP damage multiplier by distance, only for ranged units. Farther = cannot attack.
RANGE_DISSIPATION: Final[Mapping[int, float]] = MappingProxyType({1: 0.5, 2: 1.0, 3: 0.8, 4: 0.8})
MUD_DEFENSE_DIVISOR: Final[int] = 2  # [4] Effective DEF = floor(DEF / 2) on mud
MIN_DAMAGE: Final[int] = 1  # [5] every hit deals at least 1
ALLOW_BLOCK: Final[bool] = False  # [5] if True, floor(Base_Damage - DEF) <= 0 deals 0 and applies no effects

# [6] On-hit effects.
VENOM_DURATION: Final[int] = 5  # rounds; re-applying refreshes, never stacks
VENOM_ATK_DIVISOR: Final[int] = 4  # Venom damage per round = max(1, ATK // 4)
ACID_ATK_DIVISOR: Final[int] = 10  # Acid DEF shred = max(1, ATK // 10)
MIN_EFFECT_MAGNITUDE: Final[int] = 1  # the "max(1, ...)" above

# Energy.
STARTING_ENERGY: Final[int] = 0  # D6: units start empty and regenerate at turn start

# ====================================================================== ui
LOG_HISTORY: Final[int] = 200  # log lines kept by Interaction
LOG_LINES: Final[int] = 50  # log lines sent to the browser

# ====================================================================== web server
DEFAULT_HOST: Final[str] = "127.0.0.1"
DEFAULT_PORT: Final[int] = 8000
STATIC_URL_PATH: Final[str] = "/static"
INDEX_FILE: Final[str] = "index.html"
MAX_BOT_STEPS: Final[int] = 10_000  # safety limit for run_bots()
SESSIONS_KEY: Final[str] = "beastborn.sessions"  # Flask app.extensions key
DEFAULT_TTL_SECONDS: Final[int] = 30 * 60  # idle time before a game expires
DEFAULT_MAX_SESSIONS: Final[int] = 100  # games kept in memory at once
ENV_SESSION_TTL: Final[str] = "BEASTBORN_SESSION_TTL"
ENV_MAX_GAMES: Final[str] = "BEASTBORN_MAX_GAMES"
GAME_NOT_FOUND: Final[str] = "Game not found or expired"

# ====================================================================== web assets (tools/fetch_assets.py)
CDN: Final[str] = "https://cdn.jsdelivr.net/npm"
JQUERY_VERSION: Final[str] = "3.7.1"
BOOTSTRAP_VERSION: Final[str] = "5.3.3"
BOOTSTRAP_ICONS_VERSION: Final[str] = "1.11.3"
GAME_ICONS_VERSION: Final[str] = "1.2.4"

# target file (relative to STATIC_DIR) -> download URL
VENDOR_FILES: Final[Mapping[str, str]] = MappingProxyType({
    f"vendor/jquery/jquery-{JQUERY_VERSION}.min.js": f"{CDN}/jquery@{JQUERY_VERSION}/dist/jquery.min.js",
    "vendor/bootstrap/bootstrap.min.css": f"{CDN}/bootstrap@{BOOTSTRAP_VERSION}/dist/css/bootstrap.min.css",
    "vendor/bootstrap/bootstrap.bundle.min.js": f"{CDN}/bootstrap@{BOOTSTRAP_VERSION}/dist/js/bootstrap.bundle.min.js",
    "vendor/bootstrap-icons/bootstrap-icons.min.css":
        f"{CDN}/bootstrap-icons@{BOOTSTRAP_ICONS_VERSION}/font/bootstrap-icons.min.css",
    "vendor/bootstrap-icons/fonts/bootstrap-icons.woff2":
        f"{CDN}/bootstrap-icons@{BOOTSTRAP_ICONS_VERSION}/font/fonts/bootstrap-icons.woff2",
    "vendor/bootstrap-icons/fonts/bootstrap-icons.woff":
        f"{CDN}/bootstrap-icons@{BOOTSTRAP_ICONS_VERSION}/font/fonts/bootstrap-icons.woff",
})
GAME_ICONS_JSON: Final[str] = f"{CDN}/@iconify-json/game-icons@{GAME_ICONS_VERSION}/icons.json"
GAME_ICONS_DEFAULT_SIZE: Final[int] = 512
DOWNLOAD_TIMEOUT_SECONDS: Final[int] = 60

# target file (relative to STATIC_DIR) -> (game-icons name, author); licence CC BY 3.0, see static/CREDITS.md
ICON_FILES: Final[Mapping[str, tuple[str, str]]] = MappingProxyType({
    "img/units/boss.svg": ("ogre", "Delapouite"),
    "img/units/big_rat.svg": ("rat", "Delapouite"),
    "img/units/peasant.svg": ("farmer", "Delapouite"),
    "img/units/archer.svg": ("archer", "Delapouite"),
    "img/terrain/grass.svg": ("grass", "Delapouite"),
    "img/terrain/swamp.svg": ("swamp", "Delapouite"),
    "img/terrain/hills.svg": ("hills", "Delapouite"),
    "img/effects/venom.svg": ("poison-bottle", "Lorc"),
    "img/effects/acid.svg": ("acid", "Sbed"),
})
