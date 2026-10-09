from dataclasses import replace

import pytest

from beastborn.domain import UnitStats, load_roster


@pytest.fixture(scope="session")
def roster():
    return load_roster()


# Simple, round-numbered stats for scenario tests. Soldier vs Soldier on flat grass: 4 - 1 = 3 damage.
SOLDIER = UnitStats(key="soldier", name="Soldier", code="S", hp=10, atk=4, en_atk=2, defense=1, max_en=10, reg_en=6)
KING = replace(SOLDIER, key="king", name="King", code="K", hp=20, is_boss=True)
ARCHER = replace(SOLDIER, key="archer", name="Archer", code="A", atk=8, attack_range=4, move_penalty=1)
