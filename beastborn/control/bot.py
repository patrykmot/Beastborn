"""The bot player: alpha-beta search (ALG) over whole unit turns.

One ply = one unit turn: every action from ``GameStateMachine.find_all_possible_actions`` plays the active unit's whole
turn, then the GSM gives the next unit its turn. The search tries an action with ``submit`` and always undoes it with
``rollback`` (in a ``finally``), so the game is exactly the same after the bot has chosen; ``choose_command`` checks
this every time.

Turns do not strictly alternate between players (a side with more units left plays several units in a row), so a
node maximises when the bot's player is to move and minimises otherwise - never by depth parity.
"""
from __future__ import annotations

from beastborn import constant as C
from beastborn.control.bot_voice import BotVoice, Mood
from beastborn.control.controller import PlayerController
from beastborn.game.commands import Command, CommandResult, CommandType, WrapperCommand
from beastborn.game.state import Phase
from beastborn.game.state_machine import GameStateMachine
from beastborn.game.view import GameView


def _parts(command: Command) -> tuple[Command, ...]:
    """The plain commands of an action (a ``WrapperCommand`` is unpacked)."""
    return command.commands if isinstance(command, WrapperCommand) else (command,)


def _order_key(command: Command) -> tuple[int, int]:
    """Strong actions first (alpha-beta cuts more): more attacks, then moves, then waiting."""
    kinds: list[CommandType] = [c.kind for c in _parts(command)]
    return -kinds.count(CommandType.ATTACK), -kinds.count(CommandType.MOVE)


class BotPlayerController(PlayerController):
    """Chooses the active unit's whole turn with alpha-beta search; says something after every turn.

    ``evaluate`` (score of the bot minus score of its opponent) is the one place that defines a good board.
    """

    def __init__(self, depth: int = C.BOT_SEARCH_DEPTH, voice: BotVoice | None = None):
        if depth < 1:
            raise ValueError("The search depth must be at least 1")
        self.depth: int = depth
        self.last_message: str | None = None  # what the bot says about the command it chose last
        self.last_value: int | None = None  # search value of that command (for logs, tests and future moods)
        self._voice: BotVoice = voice or BotVoice()
        self._spoke: bool = False  # has the bot said its START line yet

    # ------------------------------------------------------------------ PlayerController
    def choose_command(self, gsm: GameStateMachine) -> Command | None:
        if gsm.phase is not Phase.AWAITING_COMMAND:
            return None
        me: int = gsm.active_player
        log_size: int = len(gsm.command_log)
        before: GameView = gsm.view()
        command, self.last_value = self._best_action(gsm, me)
        if len(gsm.command_log) != log_size or gsm.view() != before:  # the critical requirement, checked every time
            raise RuntimeError("Bot search changed the game state")
        self.last_message = self._voice.line(self._mood(command))
        return command

    # ------------------------------------------------------------------ evaluation
    def evaluate(self, gsm: GameStateMachine, me: int) -> int:
        """How good the board is for player ``me``: my score minus my opponent's (positive = good for me)."""
        return gsm.score(me) - gsm.score(self._opponent(gsm, me))

    def _terminal_value(self, gsm: GameStateMachine, me: int, depth: int) -> int:
        """A won game is worth more the sooner it comes (a lost one costs more the sooner it comes)."""
        winner: int | None = gsm.view().winner
        if winner is None:
            return self.evaluate(gsm, me)
        return C.BOT_WIN_SCORE + depth if winner == me else -C.BOT_WIN_SCORE - depth

    @staticmethod
    def _opponent(gsm: GameStateMachine, me: int) -> int:
        """The other player (a bot plays only in 2-player games, ``constant.BOT_PLAYERS``)."""
        return next(p.index for p in gsm.view().players if p.index != me)

    # ------------------------------------------------------------------ search (ALG)
    def _best_action(self, gsm: GameStateMachine, me: int) -> tuple[Command, int | None]:
        """The best action at the root (the first one of the best value); no search if there is only one."""
        actions: list[Command] = gsm.find_all_possible_actions()
        if not actions:
            raise RuntimeError("No possible action while the game accepts commands")
        if len(actions) == 1:
            return actions[0], None
        best: Command = actions[0]
        best_value: int = -C.BOT_WIN_SCORE * 2
        alpha: int = best_value
        for action in sorted(actions, key=_order_key):
            value: int = self._try(gsm, action, me, self.depth - 1, alpha, C.BOT_WIN_SCORE * 2)
            if value > best_value:
                best, best_value = action, value
                alpha = max(alpha, value)
        return best, best_value

    def _alphabeta(self, gsm: GameStateMachine, me: int, depth: int, alpha: int, beta: int) -> int:
        if gsm.phase is Phase.GAME_OVER:
            return self._terminal_value(gsm, me, depth)
        if depth == 0:
            return self.evaluate(gsm, me)
        maximising: bool = gsm.active_player == me
        best: int = -C.BOT_WIN_SCORE * 2 if maximising else C.BOT_WIN_SCORE * 2
        for action in sorted(gsm.find_all_possible_actions(), key=_order_key):
            value: int = self._try(gsm, action, me, depth - 1, alpha, beta)
            if maximising:
                best = max(best, value)
                alpha = max(alpha, value)
            else:
                best = min(best, value)
                beta = min(beta, value)
            if alpha >= beta:
                break
        return best

    def _try(self, gsm: GameStateMachine, action: Command, me: int, depth: int, alpha: int, beta: int) -> int:
        """Value of ``action``: submit it, search on, and always roll it back."""
        log_size: int = len(gsm.command_log)
        result: CommandResult = gsm.submit(action)
        if not result.ok:  # cannot happen: possible actions are exactly what submit accepts
            if len(gsm.command_log) > log_size:  # a wrapper rejected part-way is logged: undo it too
                gsm.rollback()
            raise RuntimeError(f"Possible action {action!r} was rejected: {result.error}")
        try:
            return self._alphabeta(gsm, me, depth, alpha, beta)
        finally:
            gsm.rollback()

    # ------------------------------------------------------------------ voice
    def _mood(self, command: Command) -> Mood:
        if not self._spoke:
            self._spoke = True
            return Mood.START
        if any(c.kind is CommandType.ATTACK for c in _parts(command)):
            return Mood.ATTACK
        return Mood.DEFENSE
