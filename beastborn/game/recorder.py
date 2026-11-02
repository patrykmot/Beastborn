"""``GameRecorder``: writes down a game while it is played, as a BGN ``GameRecord`` (game/bgn.py)."""
from __future__ import annotations

import time

from beastborn.domain.position import Position
from beastborn.game import events as ev
from beastborn.game.bgn import BgnEntry, GameRecord
from beastborn.game.commands import Command, CommandResult
from beastborn.game.listener import GameListener
from beastborn.game.state import Phase
from beastborn.game.state_machine import GameStateMachine
from beastborn.game.view import GameView


class GameRecorder(GameListener):
    """Records a game made by ``new_game``: ``GameRecorder(gsm)`` right after ``new_game``, ``record()`` at any time.

    It listens to the GSM and follows its command log through every ``submit`` and ``rollback``, so what a bot or
    strategy tries and undoes during its search is never recorded. Per command it keeps only the command, its events
    and the active unit's tile; the BGN entries are made in ``record()``.
    """

    def __init__(self, gsm: GameStateMachine, game_id: int | None = None):
        """``game_id``: default the current time in milliseconds since the Unix epoch."""
        if gsm.phase is Phase.SETUP or gsm.command_log:
            raise ValueError("Attach a GameRecorder to a started game before its first command")
        if gsm.view().seed is None:
            raise ValueError("Only a game made by new_game (it has a seed) can be recorded")
        self.gsm: GameStateMachine = gsm
        self.game_id: int = time.time_ns() // 1_000_000 if game_id is None else game_id
        # one per command of gsm.command_log: (the command, its events, the active unit's tile before it)
        self._played: list[tuple[Command, tuple[ev.GameEvent, ...], Position]] = []
        self._origin: Position | None = _active_tile(gsm)  # where the active unit stands now
        gsm.add_listener(self)

    def on_command(self, gsm: GameStateMachine, command: Command, result: CommandResult) -> None:
        assert self._origin is not None, "the GSM accepts commands only while a unit has its turn"
        self._played.append((command, result.events, self._origin))
        self._origin = _active_tile(gsm)

    def on_rollback(self, gsm: GameStateMachine, command: Command) -> None:
        _command, _events, self._origin = self._played.pop()

    def record(self) -> GameRecord:
        """The game so far."""
        if [command for command, _events, _origin in self._played] != self.gsm.command_log:
            raise RuntimeError("The recorder missed commands of its game")
        view: GameView = self.gsm.view()
        assert view.seed is not None, "checked in __init__"
        return GameRecord(
            game_id=self.game_id,
            seed=view.seed,
            width=view.board.width,
            height=view.board.height,
            player_names=tuple(player.name for player in view.players),
            entries=tuple(BgnEntry.from_command(command, origin, events) for command, events, origin in self._played),
        )


def _active_tile(gsm: GameStateMachine) -> Position | None:
    if gsm.phase is not Phase.AWAITING_COMMAND or gsm.active_unit is None:
        return None
    return gsm.unit_position(gsm.active_unit)
