"""Listeners: objects the Game State Machine tells about every change of its command log.

Give them to ``GameStateMachine(state, engine, listeners=[...])`` or ``gsm.add_listener(listener)``. The GSM calls
them after every accepted command and after every ``rollback``, so a listener sees the same command log as the GSM,
also while a bot or a strategy tries commands (``submit``) and undoes them (``rollback``) during its search.
A listener must not submit or roll back commands itself.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from beastborn.game.commands import Command, CommandResult

if TYPE_CHECKING:  # the GSM imports this module
    from beastborn.game.state_machine import GameStateMachine


class GameListener(ABC):
    @abstractmethod
    def on_command(self, gsm: GameStateMachine, command: Command, result: CommandResult) -> None:
        """``command`` was added to ``gsm.command_log``: it is the logged command (an attack with the tile it was
        made from, a ``WrapperCommand`` rejected part-way with only the commands that ran), ``result`` what
        ``submit`` returned. ``gsm`` is already in the state after the command.
        """

    @abstractmethod
    def on_rollback(self, gsm: GameStateMachine, command: Command) -> None:
        """``command``, the last one in the command log, was undone; ``gsm`` is in the state before it again."""
