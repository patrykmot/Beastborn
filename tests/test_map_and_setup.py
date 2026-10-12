import random
from collections import Counter

import pytest

from beastborn import constant as C
from beastborn.domain import Position, TerrainType, load_roster
from beastborn.game import Phase, new_game
from beastborn.game.setup import make_army
from beastborn.game.map_generator import corner_positions, generate_board, spawn_zone


def test_army_size_comes_from_constants(roster, monkeypatch):
    monkeypatch.setattr(C, "BIG_RATS_PER_PLAYER", 0)
    monkeypatch.setattr(C, "PEASANTS_PER_PLAYER", 3)
    monkeypatch.setattr(C, "ARCHERS_PER_PLAYER", 2)
    assert [u.key for u in make_army(roster)] == [C.UNIT_BOSS] + [C.UNIT_PEASANT] * 3 + [C.UNIT_ARCHER] * 2


def test_units_file_has_every_army_unit():
    real = load_roster()  # beastborn/data/units.json: only its keys are checked, never its stats
    assert real.boss.key == C.UNIT_BOSS
    assert {C.UNIT_BOSS, C.UNIT_BIG_RAT, C.UNIT_PEASANT, C.UNIT_ARCHER} <= {u.key for u in real}
    assert len(new_game(2, seed=1, roster=real).view().units) == 2 * (1 + C.UNITS_PER_PLAYER)


@pytest.mark.parametrize("players", [0, 1, 5])
def test_invalid_player_count(players):
    with pytest.raises(ValueError):
        new_game(players, seed=1)


def test_two_players_start_in_opposite_corners():
    assert corner_positions(12, 12, 2) == [Position(0, 0), Position(11, 11)]


@pytest.mark.parametrize("seed", range(25))
def test_generated_board_rules(seed):
    board = generate_board(12, 12, 4, random.Random(seed))
    assert (board.width, board.height) == (12, 12)
    for pos in board.positions():
        tile = board.tile(pos)
        assert 0 <= tile.elevation <= 2
        for n in board.neighbours(pos):
            assert abs(tile.elevation - board.tile(n).elevation) <= 1  # smoothing
    for corner in corner_positions(12, 12, 4):
        for pos in spawn_zone(corner, 12, 12, 3):
            assert board.tile(pos).terrain is TerrainType.GRASS and board.tile(pos).elevation == 0


def test_maps_have_hills_and_mud():
    tiles = [generate_board(12, 12, 2, random.Random(s)) for s in range(10)]
    all_tiles = [b.tile(p) for b in tiles for p in b.positions()]
    assert any(t.elevation == 2 for t in all_tiles)
    assert any(t.terrain is TerrainType.MUD for t in all_tiles)


def test_same_seed_same_game():
    a, b = new_game(3, seed=123).view(), new_game(3, seed=123).view()
    assert str(a.board) == str(b.board)
    assert [(u.stats.key, u.position) for u in a.units] == [(u.stats.key, u.position) for u in b.units]
    c = new_game(3, seed=124).view()
    assert str(a.board) != str(c.board)


def test_custom_map_size():
    gsm = new_game(2, seed=1, width=16, height=10)
    assert (gsm.view().board.width, gsm.view().board.height) == (16, 10)
    with pytest.raises(ValueError):
        new_game(2, seed=1, width=4, height=4)
