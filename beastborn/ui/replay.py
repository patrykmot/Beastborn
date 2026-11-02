"""Frontend-neutral replay of a recorded game (BGN, game/bgn.py): step forwards and backwards, jump anywhere.

An ``Interaction`` with a ``ReplayController`` on every seat plays the record's entries one ``tick()`` at a time,
so the board, the log lines and the last moves and attacks (``last_actions``) are the same as in the live game.
Going back builds the game again and plays it up to the wanted entry (a few milliseconds per hundred entries).
"""
from __future__ import annotations

from beastborn.control.replay import ReplayController
from beastborn.engine.interface import CalculationEngine
from beastborn.game.bgn import BgnError, GameRecord, replay
from beastborn.game.state import Phase
from beastborn.game.state_machine import GameStateMachine
from beastborn.game.view import GameView
from beastborn.ui.interaction import Interaction, Step


class Replay:
    def __init__(self, record: GameRecord, engine: CalculationEngine | None = None):
        self.record: GameRecord = record
        self._engine: CalculationEngine | None = engine
        self.gsm: GameStateMachine
        self.interaction: Interaction
        self.error: str | None = None  # why the last entry could not be played (the record does not fit the game)
        self._outcome: tuple[bool, int | None] | None = None
        self._restart()

    def _restart(self) -> None:
        self.gsm = self.record.new_game(self._engine)
        self.interaction = Interaction(self.gsm, ReplayController.for_players(self.record))
        self.error = None

    # ------------------------------------------------------------------ where we are
    @property
    def position(self) -> int:
        """Entries played so far (0 = the start of the game)."""
        return len(self.gsm.command_log)

    @property
    def length(self) -> int:
        return len(self.record.entries)

    @property
    def at_end(self) -> bool:
        """No entry can be played any more: the last one was played, or the next one does not fit the game."""
        return self.position >= self.length or self.error is not None

    @property
    def view(self) -> GameView:
        return self.interaction.view

    @property
    def last_actions(self) -> list[Step]:
        """Moves and attacks of the entry played last (empty at the start)."""
        return self.interaction.last_actions

    @property
    def log(self) -> list[str]:
        return list(self.interaction.log)

    def scores(self) -> list[int]:
        """Board score of every player, in player order."""
        return [self.gsm.score(player.index) for player in self.view.players]

    # ------------------------------------------------------------------ moving
    def step(self) -> bool:
        """Play the next entry. False at the end, or when the entry does not fit the game (see ``error``)."""
        if self.at_end:
            return False
        number: int = self.position + 1
        played: bool
        try:
            played, reason = self.interaction.tick(), self.interaction.message  # message: why submit rejected it
        except BgnError as error:
            played, reason = False, str(error)
        if not played:
            self.error = f"Entry {number}: {reason or 'cannot be played'}"
        return played

    def back(self) -> bool:
        """Go back one entry. False at the start."""
        if self.position == 0:
            return False
        self.goto(self.position - 1)
        return True

    def goto(self, index: int) -> None:
        """Show the game after ``index`` entries (clamped to 0..length)."""
        index = max(0, min(index, self.length))
        if index < self.position or (self.error is not None and index != self.position):
            self._restart()
        while self.position < index and self.step():
            pass

    # ------------------------------------------------------------------ result
    def outcome(self) -> tuple[bool, int | None]:
        """How the recorded game ends: (it ended, the winner). Not ended = stopped before a side won."""
        if self._outcome is None:
            try:
                end: GameView = replay(self.record, self._engine).view()
                self._outcome = end.phase is Phase.GAME_OVER, end.winner
            except BgnError:
                self._outcome = False, None
        return self._outcome

    def result_text(self) -> str:
        """E.g. "Learner wins", "No winner", "Stopped after 300 commands: no winner"."""
        ended, winner = self.outcome()
        if not ended:
            return f"Stopped after {self.length} commands: no winner"
        return "No winner" if winner is None else f"{self.record.player_names[winner]} wins"
