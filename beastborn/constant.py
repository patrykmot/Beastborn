"""Every constant of the Beastborn package, in one place.

This module is a leaf: it imports nothing from ``beastborn``, so any layer (domain, engine,
game, control, ui) may import it without creating an import cycle or breaking the layering
rules checked by tests/test_architecture.py.

The rule numbers (movement, combat, effects, energy) are read directly by the Calculation Engine
(``engine.standard_engine``): change a value here and every game uses it.
Map generation and turn settings are read directly too. Only the map size is chosen per game
(``new_game(width=..., height=...)``, the browser picks one of MAP_SIZES); DEFAULT_MAP_SIZE is used otherwise.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Final, Literal, get_args

# =============================================s========================= application
GAME_NAME: Final[str] = "Beastborn"
GAME_VERSION: Final[str] = "0.10"
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

# ====================================================================== game setup
MapSize = Literal[10, 12, 16]
MAP_SIZES: Final[tuple[int, ...]] = get_args(MapSize)
DEFAULT_MAP_SIZE: Final[MapSize] = 12  # used when new_game() gets no width / height
# Every army: one Boss (the unit with "boss": true) + these units next to it, same for every player.
BIG_RATS_PER_PLAYER: Final[int] = 1
PEASANTS_PER_PLAYER: Final[int] = 1
ARCHERS_PER_PLAYER: Final[int] = 1
UNITS_PER_PLAYER: Final[int] = BIG_RATS_PER_PLAYER + PEASANTS_PER_PLAYER + ARCHERS_PER_PLAYER  # without the Boss
MAX_ELEVATION: Final[int] = 2
SMOOTH_ELEVATION: Final[bool] = True  # neighbouring tiles differ by at most 1 level (D8)
HILLS_PER_100_TILES: Final[int] = 4
MUD_PATCHES_PER_100_TILES: Final[int] = 4
SPAWN_ZONE_SIZE: Final[int] = 3  # flat grass square in each starting corner
MAX_ATTACKS_PER_TURN: Final[int | None] = None  # None = limited only by energy (D3)
# An attack on an enemy out of range first moves the attacker to a tile it can hit from, if its energy pays for
# the move and the attack. The tile: cheapest move, then most damage, then best own DEF there, then top-left first.
AUTO_MOVE_TO_ATTACK: Final[bool] = True

# ====================================================================== seeds
SEED_RANGE: Final[int] = 1_000_000  # random seeds are drawn from [0, SEED_RANGE)
MAX_SEED: Final[int] = 2**31 - 1  # largest seed accepted from the web client

# ====================================================================== map generator
HILL_BASE_ELEVATION: Final[int] = 1  # the diamond around a hill's peak
MIN_HILLS: Final[int] = 1
MIN_MUD_PATCHES: Final[int] = 1
MUD_WALK_MIN_STEPS: Final[int] = 3
MUD_WALK_MAX_STEPS: Final[int] = 6
TILES_PER_DENSITY_UNIT: Final[int] = 100  # "HILLS_PER_100_TILES" etc.

# ====================================================================== unit keys ("key" in units.json)
UNIT_BOSS: Final[str] = "boss"
UNIT_BIG_RAT: Final[str] = "big_rat"
UNIT_PEASANT: Final[str] = "peasant"
UNIT_ARCHER: Final[str] = "archer"

# ====================================================================== unit data defaults (units.json)
DEFAULT_ATTACK_RANGE: Final[int] = 1
DEFAULT_MOVE_PENALTY: Final[int] = 0
MELEE_RANGE: Final[int] = 1  # attack_range > MELEE_RANGE = ranged unit

# ====================================================================== rules (read by the Calculation Engine)
# Movement: energy per tile; a unit's move_penalty is added to every step.
EN_NT: Final[int] = 2  # normal terrain
EN_DT: Final[int] = 3  # difficult terrain
EN_MU: Final[int] = 2  # extra per elevation level gained (uphill)
EN_MD_NORMAL: Final[int] = 2  # moving downhill into normal terrain
EN_MD_DIFFICULT: Final[int] = 5  # moving downhill into difficult terrain (2 + 3 penalty)
DIFFICULT_TERRAINS: Final[frozenset[str]] = frozenset({"mud"})  # TerrainType values that cost EN_DT / EN_MD_DIFFICULT

# Combat (docs/game_mechanics.md, section numbers in brackets).
# [2] Momentum = ATK + ELEVATION_COEF * ATK * Elevation_Modifier
# Damage bonus per elevation level: 0.3 = +30% ATK per level above the target, -30% per level below.
# Change this one number to rebalance height. Used exactly (0.3 -> 3/10), so no float rounding errors.
ELEVATION_COEF: Final[float] = 0.3
# [3] RP damage multiplier by distance, only for ranged units. Farther = cannot attack.
RANGE_DISSIPATION: Final[Mapping[int, float]] = MappingProxyType({1: 0.5, 2: 1.0, 3: 0.8, 4: 0.8})
# [4] Effective DEF = floor(DEF / divisor) on these terrains (TerrainType value -> divisor); others: DEF
TERRAIN_DEFENSE_DIVISOR: Final[Mapping[str, int]] = MappingProxyType({"mud": 2})
MIN_DAMAGE: Final[int] = 1  # [5] every hit deals at least 1
ALLOW_BLOCK: Final[bool] = False  # [5] if True, floor(Base_Damage - DEF) <= 0 deals 0 and applies no effects

# [6] On-hit effects.
VENOM_DURATION: Final[int] = 5  # rounds; one stack per attacker (re-hit refreshes it), every stack ticks
VENOM_ATK_DIVISOR: Final[int] = 2  # Venom damage per round = max(1, ATK // VENOM_ATK_DIVISOR)
ACID_ATK_DIVISOR: Final[int] = 10  # Acid DEF shred = max(1, ATK // 10)
MIN_EFFECT_MAGNITUDE: Final[int] = 1  # the "max(1, ...)" above

# Energy.
STARTING_ENERGY: Final[int] = 0  # D6: units start empty and regenerate at turn start

# Score of a board for one player (CalculationEngine.score), used by bots to compare moves. Higher = better.
SCORE_PER_MISSING_HP: Final[int] = 1  # per HP an opponent's unit (not a Boss) has lost
SCORE_PER_MISSING_BOSS_HP: Final[int] = 10  # per HP an opponent's Boss has lost
SCORE_PER_ELEVATION_LEVEL: Final[int] = 0  # per level above the ground each of my units stands on
SCORE_PER_KILL: Final[int] = 10  # extra per opponent's unit that has fallen (killed, or its Boss died)

# ====================================================================== bot (beastborn/control/bot.py)
BOT_SEARCH_DEPTH: Final[int] = 8  # unit turns the bot searches ahead (one ply = one whole unit turn)
AI_MAX_RETURNED_COMMANDS: Final[int | None] = 2  # find_all_possible_actions returns only the best N (None = all)
BOT_WORKERS: Final[int | None] = None  # processes for one bot search: None = all CPU cores, 1 = no extra processes
BOT_WIN_SCORE: Final[int] = 1_000_000  # search value of a won game (a lost one = minus this); far above any score
MAX_BOTS: Final[int] = 1  # bots per game
BOT_PLAYERS: Final[int] = 2  # a bot can play only in games with exactly this many players (2-player search)
BOT_NAME_TEMPLATE: Final[str] = "Player {number} (Bot)"  # number = index + 1
BOT_LINES_FILE: Final[Path] = PACKAGE_DIR / "data" / "bot_lines.json"  # what the bot says, by mood

# ====================================================================== AI (control/ai_find_action_strategy.py)
# A small tinygrad network rates the board after each possible action; the bot's strategy lists actions best first.
AI_BOARD_SIZE: Final[int] = 10  # the network reads only AI_BOARD_SIZE x AI_BOARD_SIZE boards
AI_CHANNELS: Final[int] = 10  # input planes: terrain (2), my units (3), enemy units (3), my Boss, enemy Boss
AI_FILTERS: Final[int] = 32  # filters of every 3x3 convolution
AI_BLOCKS: Final[int] = 3  # residual blocks
AI_SCORE_SCALE: Final[int] = 100  # value of an afterstate = score difference / AI_SCORE_SCALE + network
AI_WIN_REWARD: Final[float] = 5.0  # training reward for a won game (minus this for a lost one)
AI_GAMMA: Final[float] = 0.95  # discount of the next afterstate's value (low = only rewards a few turns away count)
AI_EPSILON: Final[float] = 0.05  # training: chance of a random action instead of the best one
AI_LEARNING_RATE: Final[float] = 1e-3  # Adam
AI_MEMORY_SIZE: Final[int] = 200_000  # replay memory (transitions)
AI_BATCH_SIZE: Final[int] = 128  # transitions per training step
AI_TRANSITIONS_PER_TRAIN_STEP: Final[int] = 4  # training: one train_step per N transitions a finished game stored
AI_STEP_COST: Final[float] = 0.02  # training: reward taken off every learner turn (= 1 HP of score): be quick
AI_PROGRESS_REWARD: Final[float] = 0.2  # training: x how much closer (0..1 of width + height) a unit got to the enemy Boss

AI_MIN_BATCH: Final[int] = 16  # network calls are padded to 16, 32, 64, ... so only a few shapes get compiled
AI_WEIGHTS_FILE: Final[Path] = PACKAGE_DIR / "data" / "ai_bot.safetensors"

# ====================================================================== ui
LOG_HISTORY: Final[int] = 200  # log lines kept by Interaction
LOG_LINES: Final[int] = 50  # log lines sent to the browser

# ====================================================================== web server
LOG_FORMAT: Final[str] = "%(asctime)s %(levelname)s %(name)s: %(message)s"  # server console (e.g. bot searches)
DEFAULT_HOST: Final[str] = "127.0.0.1"
DEFAULT_PORT: Final[int] = 8000
STATIC_URL_PATH: Final[str] = "/static"
INDEX_FILE: Final[str] = "index.html"
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
    f"img/units/{UNIT_BOSS}.svg": ("ogre", "Delapouite"),
    f"img/units/{UNIT_BIG_RAT}.svg": ("rat", "Delapouite"),
    f"img/units/{UNIT_PEASANT}.svg": ("farmer", "Delapouite"),
    f"img/units/{UNIT_ARCHER}.svg": ("archer", "Delapouite"),
    "img/terrain/grass.svg": ("grass", "Delapouite"),
    "img/terrain/swamp.svg": ("swamp", "Delapouite"),
    "img/terrain/hills.svg": ("hills", "Delapouite"),
    "img/effects/venom.svg": ("poison-bottle", "Lorc"),
    "img/effects/acid.svg": ("acid", "Sbed"),
})
