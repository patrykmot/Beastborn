from dataclasses import replace

import pytest

from beastborn.domain import UnitStats, load_roster


@pytest.fixture(scope="session")
def roster():
    return load_roster()


# Simple, round-numbered stats for scenario tests.
SOLDIER = UnitStats(key="soldier", name="Soldier", code="S", hp=10, atk=2, en_atk=2, defense=1, max_en=10, reg_en=6)
KING = replace(SOLDIER, key="king", name="King", code="K", hp=20, is_boss=True)
