"""Who decides the moves for a player.

Humans act through a frontend. A bot (added later) implements ``choose_command`` for the GSM's
active unit (``gsm.active_unit``) and uses the same GSM queries the UI uses (reachable_tiles, attack_plans).
``gsm.find_all_possible_actions()`` lists every whole turn the active unit can play, one command each.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from beastborn.game.commands import Command, EndTurnCommand
from beastborn.game.state_machine import GameStateMachine


class PlayerController(ABC):
    @property
    def is_human(self) -> bool:
        return False

    @abstractmethod
    def choose_command(self, gsm: GameStateMachine) -> Command | None:
        """Next command for the active unit (owned by the active player), or None to wait (e.g. for human input)."""


class HumanController(PlayerController):
    """Commands come from the frontend's input."""

    @property
    def is_human(self) -> bool:
        return True

    def choose_command(self, gsm: GameStateMachine) -> Command | None:
        return None


class PassController(PlayerController):
    """Trivial placeholder bot: ends every unit's turn without acting."""

    def choose_command(self, gsm: GameStateMachine) -> Command | None:
        return EndTurnCommand()
