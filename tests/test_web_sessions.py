import pytest

from beastborn.game import EndTurnCommand, new_game
from beastborn.ui.web.sessions import SessionStore, TooManySessions


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_create_get_delete():
    store = SessionStore()
    session = store.create(new_game(2, seed=1))
    assert len(session.id) == 32
    assert store.get(session.id) is session
    assert store.delete(session.id)
    assert store.get(session.id) is None
    assert not store.delete(session.id)


def test_games_are_independent():
    store = SessionStore()
    a = store.create(new_game(2, seed=1))
    b = store.create(new_game(2, seed=1))
    assert a.id != b.id
    assert a.interaction.gsm is not b.interaction.gsm
    a.interaction.gsm.submit(EndTurnCommand())
    assert a.interaction.gsm.active_player == 1
    assert b.interaction.gsm.active_player == 0


def test_idle_games_expire():
    clock = FakeClock()
    store = SessionStore(ttl_seconds=60, clock=clock)
    session = store.create(new_game(2, seed=1))
    clock.now += 59
    assert store.get(session.id) is session  # access refreshes the timer
    clock.now += 59
    assert store.get(session.id) is session
    clock.now += 61
    assert store.get(session.id) is None
    assert len(store) == 0


def test_expired_games_are_purged_on_create():
    clock = FakeClock()
    store = SessionStore(ttl_seconds=60, clock=clock)
    store.create(new_game(2, seed=1))
    clock.now += 61
    store.create(new_game(2, seed=2))
    assert len(store) == 1


def test_max_sessions():
    store = SessionStore(max_sessions=2)
    store.create(new_game(2, seed=1))
    store.create(new_game(2, seed=2))
    with pytest.raises(TooManySessions):
        store.create(new_game(2, seed=3))
