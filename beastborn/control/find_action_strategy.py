"""Which actions a bot considers: ``FindActionStrategy`` lists the active unit's possible whole turns.

One action = one command that plays the active unit's whole turn (the next unit gets its turn after it), so one action
is one ply of the bot's search. ``BotPlayerController`` takes a strategy (default: ``RandomAllFindActionStrategy``).

``AllFindActionStrategy`` lists every way to play the turn, in a fixed order (REQ_LOG_08_01).
``RandomAllFindActionStrategy`` lists the same actions in random order, so the bot picks among equally good ones at
random.

Read only (REQ_LOG_08_02): a strategy must leave the game exactly as it was. The two strategies here try every attack
with ``submit`` and always undo it with ``rollback`` (in a ``finally``), so the list cannot differ from what ``submit``
accepts. The GSM's ``version``, ``command_log`` and state are the same afterwards.
"""
from __future__ import annotations

import random
import weakref
from abc import ABC, abstractmethod
from typing import Any

from beastborn.domain.position import Position
from beastborn.game.commands import Command, CommandResult, EndTurnCommand, MoveCommand, WrapperCommand
from beastborn.game.pathfinding import PathInfo
from beastborn.game.state import Phase
from beastborn.game.state_machine import GameStateMachine

Turn = tuple[Command, ...]  # the commands of one whole unit turn, in order (the last one is EndTurnCommand)
_WAIT: Turn = (EndTurnCommand(),)  # the turn without acting
_Memo = dict[tuple[object, ...], list[Turn]]


class FindActionStrategy(ABC):
    """Lists the actions (whole unit turns) a bot considers in the current state of a game.

    A strategy may cache, but must leave the game exactly as it was (see the module docstring).
    It is sent to worker processes with the bot, so it must be picklable (defined at module level).
    """

    @abstractmethod
    def find_all_possible_actions(self, gsm: GameStateMachine) -> list[Command]:
        """The actions for ``gsm.active_unit``, each one valid now and ending its turn. Empty when the game is over.

        The caller gets its own list (it may change it).
        """

    def for_worker(self, seed: int) -> FindActionStrategy:
        """The strategy a worker process of the bot uses: with its own repeatable randomness from ``seed``.

        A strategy without randomness returns itself (the default).
        """
        return self


class AllFindActionStrategy(FindActionStrategy):
    """Every way the active unit can play its whole turn, one command per way, always in the same order.

    A turn is: attacks (each may move first: ``AttackCommand`` with an explicit ``move_to``), then maybe one
    ``MoveCommand``, then always ``EndTurnCommand`` (accepted also when the GSM already ended the turn by itself).
    So every command is a ``WrapperCommand`` ending with ``EndTurnCommand``, except waiting: ``EndTurnCommand``
    alone (listed first), then the moves, then the turns starting with an attack. Moves are never chained (one
    ``MoveCommand`` already takes the cheapest path). Every distinct command sequence is listed, also those that end
    on the same board (e.g. two attacks in the other order).

    The list is cached for the last state asked about (one GSM, one ``version``) until that state changes.
    """

    def __init__(self) -> None:
        # (the GSM, its version, the actions in their usual order) of the last state asked about
        self._cache: tuple[weakref.ref[GameStateMachine], int, tuple[Command, ...]] | None = None

    def find_all_possible_actions(self, gsm: GameStateMachine) -> list[Command]:
        return list(self._ordered(gsm))

    def _ordered(self, gsm: GameStateMachine) -> tuple[Command, ...]:
        if gsm.phase is not Phase.AWAITING_COMMAND or gsm.active_unit is None:
            return ()
        cache = self._cache
        if cache is not None and cache[0]() is gsm and cache[1] == gsm.version:
            return cache[2]
        actions: tuple[Command, ...] = tuple(
            turn[0] if turn == _WAIT else WrapperCommand(turn) for turn in self._turns(gsm, {})
        )
        self._cache = (weakref.ref(gsm), gsm.version, actions)
        return actions

    def _turns(self, gsm: GameStateMachine, memo: _Memo) -> list[Turn]:
        """Every command sequence that finishes the active unit's turn from the current state (all end with end turn).

        ``memo``: a situation reached twice (e.g. the same two attacks in the other order) is searched only once.
        """
        unit_id: int | None = gsm.active_unit
        situation: tuple[object, ...] | None = gsm.turn_situation()
        assert unit_id is not None and situation is not None, "a turn is running"
        known: list[Turn] | None = memo.get(situation)
        if known is not None:
            return known
        reach: dict[Position, PathInfo] = gsm.reachable_tiles(unit_id)
        turns: list[Turn] = [_WAIT]
        turns += [(MoveCommand(unit_id, move.path[-1]), EndTurnCommand()) for move in reach.values()]
        for attack in gsm.attack_commands(unit_id, reach):
            turns += [(attack, *rest) for rest in self._turns_after(gsm, attack, memo)]
        memo[situation] = turns
        return turns

    def _turns_after(self, gsm: GameStateMachine, command: Command, memo: _Memo) -> list[Turn]:
        """Every way to finish the turn after ``command``: it is submitted, searched on, then always rolled back."""
        unit_id: int | None = gsm.active_unit
        log_size: int = len(gsm.command_log)
        result: CommandResult = gsm.submit(command)
        if not result.ok:  # cannot happen: attack_commands lists only what submit accepts
            if len(gsm.command_log) > log_size:
                gsm.rollback()
            raise RuntimeError(f"Possible action {command!r} was rejected: {result.error}")
        try:
            return [_WAIT] if gsm.turn_over(unit_id, result.events) else self._turns(gsm, memo)
        finally:
            gsm.rollback()

    def __getstate__(self) -> dict[str, Any]:
        """Pickled without the cache (it refers to a GSM, which is never sent to worker processes)."""
        state: dict[str, Any] = self.__dict__.copy()
        state["_cache"] = None
        return state


class RandomAllFindActionStrategy(AllFindActionStrategy):
    """The actions of ``AllFindActionStrategy`` in random order.

    Shuffled with ``rng`` once per state and cached, so the same state keeps the same order until it changes (a bot
    that tries actions in this order and keeps the first best one picks among equally good actions at random).
    ``random.Random(seed)`` repeats the same orders for the same game; None = a fresh random one.
    """

    def __init__(self, rng: random.Random | None = None) -> None:
        super().__init__()
        self.rng: random.Random = rng or random.Random()
        # (the GSM, its version, the shuffled actions) of the last state asked about
        self._shuffled: tuple[weakref.ref[GameStateMachine], int, tuple[Command, ...]] | None = None

    def find_all_possible_actions(self, gsm: GameStateMachine) -> list[Command]:
        ordered: tuple[Command, ...] = self._ordered(gsm)
        if not ordered:  # game over: nothing to shuffle (the rng is not used)
            return []
        cache = self._shuffled
        if cache is not None and cache[0]() is gsm and cache[1] == gsm.version:
            return list(cache[2])
        shuffled: tuple[Command, ...] = tuple(self.rng.sample(ordered, len(ordered)))
        self._shuffled = (weakref.ref(gsm), gsm.version, shuffled)
        return list(shuffled)

    def for_worker(self, seed: int) -> RandomAllFindActionStrategy:
        return RandomAllFindActionStrategy(random.Random(seed))

    def __getstate__(self) -> dict[str, Any]:
        state: dict[str, Any] = super().__getstate__()
        state["_shuffled"] = None
        return state
