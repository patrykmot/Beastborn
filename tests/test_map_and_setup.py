import random

import pytest

from beastborn.domain import Position, TerrainType
from beastborn.game import GameConfig, Phase, new_game
from beastborn.game.map_generator import corner_positions, generate_board, spawn_zone


@pytest.mark.parametrize("players", [2, 3, 4])
def test_new_game_armies(players, roster):
    gsm = new_game(players, seed=7)
    view = gsm.view()
    corners = corner_positions(12, 12, players)
    assert view.phase is Phase.AWAITING_COMMAND
    assert view.active_player == 0 and view.round == 1
    for p in range(players):
        army = view.units_of(p)
        assert len(army) == 5
        bosses = [u for u in army if u.is_boss]
        assert len(bosses) == 1 and bosses[0].position == corners[p]
        assert all(u.stats in roster.recruits for u in army if not u.is_boss)
        zone = spawn_zone(corners[p], 12, 12, 3)
        assert all(u.position in zone for u in army)
    positions = [u.position for u in view.units]
    assert len(positions) == len(set(positions))


@pytest.mark.parametrize("players", [0, 1, 5])
def test_invalid_player_count(players):
    with pytest.raises(ValueError):
        new_game(players, seed=1)


def test_two_players_start_in_opposite_corners():
    assert corner_positions(12, 12, 2) == [Position(0, 0), Position(11, 11)]


@pytest.mark.parametrize("seed", range(25))
def test_generated_board_rules(seed):
    config = GameConfig()
    board = generate_board(config, 4, random.Random(seed))
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
    tiles = [generate_board(GameConfig(), 2, random.Random(s)) for s in range(10)]
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
    gsm = new_game(2, seed=1, config=GameConfig(width=16, height=10))
    assert (gsm.view().board.width, gsm.view().board.height) == (16, 10)
    with pytest.raises(ValueError):
        GameConfig(width=4, height=4)
