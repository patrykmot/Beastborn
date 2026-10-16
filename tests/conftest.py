"""Shared test units and rule helpers.

Tests never read the stats in beastborn/data/units.json (they change with every rebalance). They use the units below:
FIXED_* have the real keys (so ``new_game()`` can build its armies) and stats that never change.

Rule numbers come from ``beastborn/constant.py`` (``C.*``), never as copies: the helpers below turn them into the
values tests expect, so a rebalance in constant.py does not break the tests. Tests that check a hand-made table or
formula text set the few rules it was worked out with (``monkeypatch``), and say so.
"""
import json
import math
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest

from beastborn import constant as C
from beastborn.domain import EffectKind, EffectSpec, Roster, UnitStats

# Simple, round-numbered stats for scenario tests. Soldier vs Soldier on flat grass: 4 - 1 = 3 damage.
SOLDIER = UnitStats(key="soldier", name="Soldier", code="S", hp=10, atk=4, en_atk=2, defense=1, max_en=10, reg_en=6)
KING = replace(SOLDIER, key="king", name="King", code="K", hp=20, is_boss=True)
ARCHER = replace(SOLDIER, key=C.UNIT_ARCHER, name="Archer", code="A", atk=8, attack_range=4, move_penalty=1)

# Rule values from constant.py, as the Calculation Engine applies them.
MAX_RANGE: int = max(C.RANGE_DISSIPATION)  # farthest distance a ranged unit can shoot


def range_multiplier(distance: int) -> Fraction:
    """RP damage multiplier of a ranged hit (exact, like the engine: 0.8 -> 4/5)."""
    return Fraction(str(C.RANGE_DISSIPATION[distance]))


def venom_per_round(atk: int) -> int:
    """HP a Venom stack takes per round."""
    return max(C.MIN_EFFECT_MAGNITUDE, atk // C.VENOM_ATK_DIVISOR)


def acid_shred(atk: int) -> int:
    """DEF one Acid hit removes."""
    return max(C.MIN_EFFECT_MAGNITUDE, atk // C.ACID_ATK_DIVISOR)


def mud_defense(defense: int) -> int:
    """Effective DEF of a unit standing in mud."""
    return defense // C.TERRAIN_DEFENSE_DIVISOR.get("mud", 1)


def hit_damage(raw: Fraction | int) -> int:
    """Final damage of a hit that is not blocked: rounded down, at least MIN_DAMAGE."""
    return max(C.MIN_DAMAGE, math.floor(raw))


def first_turn_energy(stats: UnitStats) -> int:
    """Energy of a unit at the start of its first turn (STARTING_ENERGY + regeneration, capped)."""
    return min(stats.max_en, min(stats.max_en, C.STARTING_ENERGY) + stats.reg_en)


# A fixed copy of units.json for the balance report and for new_game().
FIXED_BOSS = UnitStats(
    key=C.UNIT_BOSS, name="Boss", code="B", hp=40, atk=5, en_atk=3, defense=4, max_en=6, reg_en=3, is_boss=True,
)
FIXED_BIG_RAT = UnitStats(
    key=C.UNIT_BIG_RAT, name="Big Rat", code="R", hp=20, atk=5, en_atk=2, defense=1, max_en=10, reg_en=6,
    on_hit=(EffectSpec(EffectKind.VENOM),),
)
FIXED_PEASANT = UnitStats(
    key=C.UNIT_PEASANT, name="Peasant", code="P", hp=16, atk=6, en_atk=3, defense=2, max_en=8, reg_en=5,
)
FIXED_ARCHER = UnitStats(
    key=C.UNIT_ARCHER, name="Archer", code="A", hp=14, atk=7, en_atk=3, defense=2, max_en=8, reg_en=5,
    attack_range=4, move_penalty=1,
)
FIXED_UNITS = (FIXED_BOSS, FIXED_BIG_RAT, FIXED_PEASANT, FIXED_ARCHER)


def fixed_roster() -> Roster:
    return Roster(list(FIXED_UNITS))


def write_units_file(path: Path, units=FIXED_UNITS) -> Path:
    """Write ``units`` in the units.json format (for code that loads a file, e.g. the report app)."""
    data = {"units": [
        {
            "key": u.key, "name": u.name, "code": u.code, "hp": u.hp, "atk": u.atk, "en_atk": u.en_atk,
            "def": u.defense, "max_en": u.max_en, "reg_en": u.reg_en, "range": u.attack_range,
            "move_penalty": u.move_penalty, "boss": u.is_boss, "on_hit": [{"kind": e.kind.value} for e in u.on_hit],
        }
        for u in units
    ]}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


@pytest.fixture
def scenario_rules(monkeypatch):
    """The two rule numbers the scenario boards are drawn for: units start empty (D6) and a flat step costs 2 EN.

    Scenario tests tell stories in tiles and hits ("the target is 3 steps away: out of reach", "3 hits use up the
    energy, the turn ends by itself"); these two numbers keep them true whatever constant.py says. Every other rule
    number is read from constant.py. Used per module: ``pytestmark = pytest.mark.usefixtures("scenario_rules")``.
    Both rules themselves are tested in test_engine.py with the values from constant.py.
    """
    monkeypatch.setattr(C, "STARTING_ENERGY", 0)
    monkeypatch.setattr(C, "EN_NT", 2)


@pytest.fixture
def roster():
    return fixed_roster()


@pytest.fixture(autouse=True)
def fixed_units(monkeypatch):
    """new_game() without a roster (also behind the web API) uses FIXED_UNITS, not units.json."""
    monkeypatch.setattr("beastborn.game.setup.load_roster", lambda path=None: fixed_roster())
