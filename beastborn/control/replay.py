"""``ReplayController``: a player that plays the commands of a recorded game (BGN, game/bgn.py)."""
from __future__ import annotations

from beastborn.control.controller import PlayerController
from beastborn.game.bgn import GameRecord
from beastborn.game.commands import Command
from beastborn.game.state_machine import GameStateMachine


class ReplayController(PlayerController):
    """Plays a ``GameRecord`` in the game ``record.new_game()`` builds.

    The next command is the record's entry number ``len(gsm.command_log)``, so one controller serves every seat
    (``for_players``) and a ``gsm.rollback()`` simply makes it play the same entry again. After the last entry it
    returns None. ``BgnEntry.to_command`` raises ``BgnError`` when the game does not follow the record.
    """

    def __init__(self, record: GameRecord):
        self.record: GameRecord = record

    @classmethod
    def for_players(cls, record: GameRecord) -> dict[int, PlayerController]:
        """Controllers for ``Interaction``: one ``ReplayController`` playing every seat of the record."""
        controller: ReplayController = cls(record)
        return {player: controller for player in range(record.num_players)}

    def choose_command(self, gsm: GameStateMachine) -> Command | None:
        index: int = len(gsm.command_log)
        if index >= len(self.record.entries):
            return None
        return self.record.entries[index].to_command(gsm)
