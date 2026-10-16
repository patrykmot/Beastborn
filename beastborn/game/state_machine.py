"""The Game State Machine (GSM).

Owns all state changes: validates commands, asks the Calculation Engine for numbers,
applies the results and emits events. It contains no formulas of its own.

Turn flow: a round gives every unit on the board one turn. The GSM picks the unit (players cannot choose):
players take turns in index order (P1, P2, ..., P1, ...), and each player's units go in id order. A unit that
cannot move or attack right now is put off until no unit that can act is left this round (others may free it).
In its turn a unit regenerates, its status effects tick, then it moves and attacks until it cannot do anything
more (the GSM ends its turn) or ``EndTurnCommand`` ends it. When every unit has had its turn, the next round starts.

``rollback`` undoes the last command exactly. ``find_all_possible_actions`` lists every way the active unit can
play its whole turn, one command per way.
"""
from __future__ import annotations

from collections.abc import Callable
from itertools import count
from typing import Any

from beastborn import constant as C
from beastborn.domain.board import Board
from beastborn.domain.effects import StatusEffect
from beastborn.domain.player import Player
from beastborn.domain.position import Position
from beastborn.domain.terrain import Tile
from beastborn.domain.unit import UnitState
from beastborn.engine.interface import CalculationEngine
from beastborn.engine.results import AttackResult, CombatantSnapshot, TickResult
from beastborn.game import events as ev
from beastborn.game.attack_plan import AttackPlan
from beastborn.game.commands import (
    AttackCommand, Command, CommandResult, CommandType, EndTurnCommand, MoveCommand, WrapperCommand,
)
from beastborn.game.pathfinding import PathInfo, reachable
from beastborn.game.state import GameState, Phase
from beastborn.game.view import GameView, PlayerView, UnitView

_OUT_OF_RANGE: str = "Target is out of range"
_OUT_OF_REACH: str = "Target is out of range and cannot be reached this turn"

Turn = tuple[Command, ...]  # the commands of one whole unit turn, in order


class GameStateMachine:
    def __init__(self, state: GameState, engine: CalculationEngine):
        self._state: GameState = state
        self._engine: CalculationEngine = engine
        self.command_log: list[Command] = []  # with the seed this is enough to replay a game
        # _history[i] = (state before command_log[i], its version): what ``rollback`` goes back to
        self._history: list[tuple[GameState, int]] = []
        self._new_versions: count[int] = count(1)
        self._version: int = 0  # one number per distinct state; ``rollback`` brings the old number back
        self._actions_cache: tuple[int, tuple[Command, ...]] | None = None  # (version, find_all_possible_actions)
        self._handlers: dict[CommandType, Callable[[Any], tuple[CommandResult, Command]]] = {
            CommandType.MOVE: self._move,
            CommandType.ATTACK: self._attack,
            CommandType.END_TURN: self._end_turn,
        }

    # ------------------------------------------------------------------ properties
    @property
    def engine(self) -> CalculationEngine:
        return self._engine

    @property
    def phase(self) -> Phase:
        return self._state.phase

    @property
    def active_player(self) -> int:
        return self._state.active_player

    @property
    def active_unit(self) -> int | None:
        """The unit whose turn it is. Commands may only use this unit."""
        return self._state.active_unit

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> list[ev.GameEvent]:
        """Begin round 1 and the first unit's turn. Returns their events."""
        if self._state.phase is not Phase.SETUP:
            raise RuntimeError("Game already started")
        self._state.round = 1
        return [ev.RoundStarted(1), *self._next_turn()]

    # ------------------------------------------------------------------ queries
    def view(self) -> GameView:
        s: GameState = self._state
        units: tuple[UnitView, ...] = tuple(
            UnitView(
                id=u.id,
                owner=u.owner,
                stats=u.stats,
                position=u.position,
                hp=u.hp,
                energy=u.energy,
                defense=u.defense,
                current_defense=self._engine.current_defense(u.defense, s.board.tile(u.position)),
                effects=u.effects,
            )
            for u in s.sorted_units()
        )
        players: tuple[PlayerView, ...] = tuple(PlayerView(p.index, p.name, p.eliminated) for p in s.players)
        return GameView(s.board, units, players, s.active_player, s.active_unit, s.round, s.phase, s.winner, s.seed)

    def reachable_tiles(self, unit_id: int) -> dict[Position, PathInfo]:
        """Where the active unit can move this turn (empty for any other unit)."""
        unit: UnitState | None = self._own_unit(unit_id)
        if unit is None:
            return {}
        return self._reachable(unit, unit.energy)

    def preview_attack(self, attacker_id: int, target_id: int) -> AttackResult | None:
        """Exact outcome of an attack (combat has no RNG). Ignores energy and range."""
        attacker: UnitState | None = self._state.units.get(attacker_id)
        target: UnitState | None = self._state.units.get(target_id)
        if attacker is None or target is None:
            return None
        return self._resolve(attacker, target)

    def score(self, player: int) -> int:
        """How good the current board is for ``player`` (Calculation Engine score)."""
        return self._engine.score(self._state, player)

    def attack_targets(self, unit_id: int) -> dict[int, AttackResult]:
        """Enemies the active unit can attack right now from where it stands, with the exact outcome."""
        unit: UnitState | None = self._own_unit(unit_id)
        if unit is None:
            return {}
        return {
            target.id: self._resolve(unit, target)
            for target in self._state.units.values()
            if self._attack_error(unit, target) is None
        }

    def plan_attack(self, attacker_id: int, target_id: int, move_to: Position | None = None) -> AttackPlan | None:
        """How ``AttackCommand(attacker_id, target_id, move_to)`` would be carried out; None if it would be rejected.

        Target in range: attack from where the unit stands (it never moves then).
        Out of range: move to the tile with the cheapest move that still leaves energy for the attack; ties go to
        more damage, then better own DEF on that tile, then the tile nearest the top-left (y, then x).
        """
        attacker: UnitState | None = self._own_unit(attacker_id)
        target: UnitState | None = self._state.units.get(target_id)
        if attacker is None or target is None:
            return None
        plan, _ = self._plan_attack(attacker, target, move_to)
        return plan

    def attack_plans(self, unit_id: int) -> dict[int, AttackPlan]:
        """Every enemy the active unit can attack with one command: in range now, or after a move."""
        unit: UnitState | None = self._own_unit(unit_id)
        if unit is None:
            return {}
        origins: dict[Position, PathInfo] = self._attack_origins(unit)
        plans: dict[int, AttackPlan] = {}
        for target in self._state.sorted_units():
            plan, _ = self._plan_attack(unit, target, None, origins)
            if plan is not None:
                plans[target.id] = plan
        return plans

    # ------------------------------------------------------------------ commands
    def submit(self, command: Command) -> CommandResult:
        """Run a command for the active unit. A command that changed the state is added to ``command_log``."""
        if self._state.phase is not Phase.AWAITING_COMMAND:
            return CommandResult.rejected(f"Game is not accepting commands (phase: {self._state.phase.value})")
        if not isinstance(command, Command):
            return CommandResult.rejected(f"Unknown command: {command!r}")
        before: GameState = self._state
        self._state = before.copy()  # the command changes the copy; ``before`` is kept untouched for ``rollback``
        result: CommandResult
        logged: Command | None
        try:
            if isinstance(command, WrapperCommand):
                result, logged = self._run_wrapper(command)
            else:
                result, logged = self._run(command)
        except BaseException:
            self._state = before  # a failing command never leaves a half-changed state
            raise
        if logged is None:  # rejected: nothing changed
            self._state = before
        else:  # the state changed (also a WrapperCommand rejected part-way)
            self.command_log.append(logged)
            self._history.append((before, self._version))
            self._version = next(self._new_versions)
        return result

    def rollback(self) -> Command | None:
        """Undo the last command in ``command_log``: the state and the log are exactly as before it.

        Returns the undone command, or None if there is nothing to undo. Can be repeated down to the start of the game.
        ``submit`` keeps the state from before each command untouched and changes a copy, so a rollback brings back
        the very same objects (a unit taken from the state before a command is the live one again after its rollback).
        """
        if not self._history:
            return None
        self._state, self._version = self._history.pop()
        return self.command_log.pop()

    def _run(self, command: Command) -> tuple[CommandResult, Command | None]:
        """Run one command (not a wrapper). Returns the result and the command to log (None = nothing changed).

        An attack is logged with the tile it was made from.
        """
        handler: Callable[[Any], tuple[CommandResult, Command]] | None = self._handlers.get(command.kind)
        if handler is None:
            return CommandResult.rejected(f"Unknown command: {command!r}"), None
        result, logged = handler(command)
        if not result.ok:
            return result, None
        if command.kind is not CommandType.END_TURN:
            result = self._after_action(result)
        return result, logged

    def _run_wrapper(self, wrapper: WrapperCommand) -> tuple[CommandResult, WrapperCommand | None]:
        """Run the wrapped commands in order. On a rejected one stop: the ones before it stay done (and logged)."""
        error: str | None = self._wrapper_error(wrapper)
        if error is not None:
            return CommandResult.rejected(error), None
        unit_id: int | None = self._state.active_unit
        events: list[ev.GameEvent] = []
        done: list[Command] = []
        for number, command in enumerate(wrapper.commands, start=1):
            result: CommandResult
            logged: Command | None = None
            if self._state.phase is not Phase.AWAITING_COMMAND or self._state.active_unit != unit_id:
                result = CommandResult.rejected("The unit's turn is already over")
            else:
                result, logged = self._run(command)
            if not result.ok or logged is None:
                reason: str = f"Command {number} of {len(wrapper.commands)} rejected: {result.error}"
                return CommandResult.rejected(reason, tuple(events)), WrapperCommand(tuple(done)) if done else None
            events += result.events
            done.append(logged)
        return CommandResult(True, tuple(events)), WrapperCommand(tuple(done))

    @staticmethod
    def _wrapper_error(wrapper: WrapperCommand) -> str | None:
        """Checks done before any wrapped command runs."""
        commands: tuple[Command, ...] = wrapper.commands
        if not commands:
            return "A wrapper command needs at least one command"
        if any(not isinstance(c, Command) or c.kind is CommandType.WRAPPER for c in commands):
            return "A wrapper command can contain only move, attack and end turn commands"
        if any(c.kind is CommandType.END_TURN for c in commands[:-1]):
            return "End turn can only be the last command of a wrapper command"
        return None

    def _after_action(self, result: CommandResult) -> CommandResult:
        """After a move or an attack: the GSM ends the turn of a unit that cannot do anything more."""
        s: GameState = self._state
        s.actions_this_turn += 1
        if s.phase is not Phase.AWAITING_COMMAND or s.active_unit is None:  # the game is over
            return result
        unit: UnitState = s.units[s.active_unit]  # an attack never kills the attacker
        if self._can_act(unit, unit.energy):
            return result
        return CommandResult(True, result.events + tuple(self._end_unit_turn()))

    def _move(self, cmd: MoveCommand) -> tuple[CommandResult, MoveCommand]:
        unit, error = self._own_unit_or_error(cmd.unit_id)
        if unit is None:
            return CommandResult.rejected(error or "Unit does not exist"), cmd
        if not self._state.board.in_bounds(cmd.destination):
            return CommandResult.rejected("Destination is outside the board"), cmd
        if cmd.destination in self._state.occupied():
            return CommandResult.rejected("Destination is occupied"), cmd
        info: PathInfo | None = self.reachable_tiles(unit.id).get(cmd.destination)
        if info is None:
            return CommandResult.rejected("Not enough energy to reach that tile"), cmd
        return CommandResult(True, (self._apply_move(unit, info),)), cmd

    def _attack(self, cmd: AttackCommand) -> tuple[CommandResult, AttackCommand]:
        """Attack, moving first if needed. Returns the result and the command to log (with the tile used)."""
        attacker, error = self._own_unit_or_error(cmd.attacker_id)
        if attacker is None:
            return CommandResult.rejected(error or "Unit does not exist"), cmd
        target: UnitState | None = self._state.units.get(cmd.target_id)
        if target is None:
            return CommandResult.rejected("Target does not exist"), cmd
        plan, error = self._plan_attack(attacker, target, cmd.move_to)
        if plan is None:
            return CommandResult.rejected(error or "Cannot attack that unit"), cmd

        # The whole plan is valid: only now is the state changed (move, then the hit from the new tile).
        events: list[ev.GameEvent] = []
        if plan.move is not None:
            events.append(self._apply_move(attacker, plan.move))
        result: AttackResult = self._resolve(attacker, target)
        attacker.energy -= self._engine.attack_cost(attacker.stats)
        self._state.attacks_this_turn += 1
        self._apply_change(target, -result.damage, result.defense_change, result.defender_effects_after)

        events.append(ev.UnitAttacked(
            attacker.id, target.id, result, max(0, target.hp), attacker.energy, attacker.position, target.position
        ))
        if not target.alive:
            events += self._kill(target)
        logged: AttackCommand = cmd if plan.move is None else AttackCommand(attacker.id, cmd.target_id, plan.origin)
        return CommandResult(True, tuple(events)), logged

    def _end_turn(self, cmd: EndTurnCommand) -> tuple[CommandResult, EndTurnCommand]:
        return CommandResult(True, tuple(self._end_unit_turn())), cmd

    # ------------------------------------------------------------------ possible actions
    def find_all_possible_actions(self) -> list[Command]:
        """Every way the active unit can play its whole turn, one command per way (REQ_LOG_08_01).

        Every command is valid now and, when submitted, ends the unit's turn (the next unit gets its turn).
        A turn is: attacks (each may move first: ``AttackCommand`` with an explicit ``move_to``), then maybe one
        ``MoveCommand``, then ``EndTurnCommand`` if the unit could still act (else the GSM ends the turn by itself).
        A turn of one command is that command, a longer one is a ``WrapperCommand``. Moves are never chained (one
        ``MoveCommand`` already takes the cheapest path). Every distinct command sequence is listed, also those that
        end on the same board (e.g. two attacks in the other order). Empty when no command is accepted (game over).

        Read only (REQ_LOG_08_02): every attack is tried with ``submit`` and undone with ``rollback``, so the game is
        the same afterwards and the list cannot differ from what ``submit`` accepts. Cached until the state changes.
        """
        if self._state.phase is not Phase.AWAITING_COMMAND or self._state.active_unit is None:
            return []
        if self._actions_cache is None or self._actions_cache[0] != self._version:
            turns: list[Turn] = self._turns({})
            actions: tuple[Command, ...] = tuple(turn[0] if len(turn) == 1 else WrapperCommand(turn) for turn in turns)
            self._actions_cache = (self._version, actions)
        return list(self._actions_cache[1])

    def _turns(self, memo: dict[tuple[object, ...], list[Turn]]) -> list[Turn]:
        """Every command sequence that finishes the active unit's turn from the current state.

        ``memo``: a situation reached twice (e.g. the same two attacks in the other order) is searched only once.
        """
        unit: UnitState = self._state.units[self._state.active_unit]
        situation: tuple[object, ...] = self._situation(unit)
        known: list[Turn] | None = memo.get(situation)
        if known is not None:
            return known
        reach: dict[Position, PathInfo] = self._reachable(unit, unit.energy)
        turns: list[Turn] = [(EndTurnCommand(),)]
        turns += [self._move_turn(unit, move) for move in reach.values()]
        for attack in self._attack_commands(unit, reach):
            turns += [(attack, *rest) for rest in self._turns_after(attack, memo)]
        memo[situation] = turns
        return turns

    def _turns_after(self, command: Command, memo: dict[tuple[object, ...], list[Turn]]) -> list[Turn]:
        """Every way to finish the turn after ``command``: it is submitted, searched on, then rolled back."""
        unit_id: int | None = self._state.active_unit
        result: CommandResult = self.submit(command)
        if not result.ok:  # cannot happen: commands are built with the checks submit uses
            raise RuntimeError(f"Possible action {command!r} was rejected: {result.error}")
        try:
            turn_over: bool = any(isinstance(e, ev.TurnEnded) and e.unit_id == unit_id for e in result.events)
            return [()] if turn_over or self._state.phase is Phase.GAME_OVER else self._turns(memo)
        finally:
            self.rollback()

    def _situation(self, unit: UnitState) -> tuple[object, ...]:
        """Everything the rest of the active unit's turn depends on (its own other units do not change)."""
        enemies: tuple[tuple[object, ...], ...] = tuple(
            (u.id, u.hp, u.defense, u.effects) for u in self._state.sorted_units() if u.owner != unit.owner
        )
        return unit.position, unit.energy, self._state.attacks_this_turn, enemies

    def _attack_commands(self, unit: UnitState, reach: dict[Position, PathInfo]) -> list[AttackCommand]:
        """Every attack the unit can make now: from where it stands, and after a move to each tile it can hit from."""
        budget: int = unit.energy - self._engine.attack_cost(unit.stats)  # energy left for a move first
        commands: list[AttackCommand] = []
        for target in self._state.sorted_units():
            if self._attack_ready_error(unit, target) is not None:
                continue
            if self._in_range_from(unit, unit.position, target):
                commands.append(AttackCommand(unit.id, target.id))
            commands += [
                AttackCommand(unit.id, target.id, origin)
                for origin, move in reach.items()
                if move.cost <= budget and self._in_range_from(unit, origin, target)
            ]
        return commands

    def _move_turn(self, unit: UnitState, move: PathInfo) -> Turn:
        """A move as the last action of a turn, + ``EndTurnCommand`` if the unit could still act after it.

        Checked with ``_can_act`` (as ``submit`` does after a move) instead of submitting: a move is the last action
        anyway, so nothing has to be played on.
        """
        destination: Position = move.path[-1]
        command: MoveCommand = MoveCommand(unit.id, destination)
        if self._can_act(unit, unit.energy - move.cost, at=destination):
            return command, EndTurnCommand()
        return (command,)

    # ------------------------------------------------------------------ turn flow
    def _end_unit_turn(self) -> list[ev.GameEvent]:
        """End the active unit's turn and give the next unit its turn."""
        s: GameState = self._state
        assert s.active_unit is not None, "a turn is running"
        events: list[ev.GameEvent] = [ev.TurnEnded(s.active_player, s.active_unit, idle=s.actions_this_turn == 0)]
        return events + self._next_turn()

    def _next_turn(self) -> list[ev.GameEvent]:
        """Give the next unit its turn (a new round when every unit had one).

        A unit that dies at its turn start or cannot act is skipped. If a whole cycle of units cannot act,
        the next one still gets its turn (its player can only end it), so the game never loops forever.
        """
        s: GameState = self._state
        events: list[ev.GameEvent] = []
        skipped: int = 0
        while True:
            if not s.units:  # only possible without Bosses (e.g. the balance report): nobody is left to play
                s.phase, s.winner = Phase.GAME_OVER, None
                return [*events, ev.GameOver(None)]
            unit: UnitState | None = self._next_unit()
            if unit is None:  # every unit had its turn
                s.round += 1
                s.acted_this_round = set()
                events.append(ev.RoundStarted(s.round))
                continue
            events += self._begin_turn(unit)
            if s.phase is Phase.GAME_OVER:
                return events
            if not unit.alive:  # killed by a status effect at its turn start
                continue
            if self._can_act(unit, unit.energy) or skipped >= len(s.units):
                s.phase = Phase.AWAITING_COMMAND
                return events
            events.append(ev.TurnEnded(unit.owner, unit.id, idle=True))
            skipped += 1

    def _next_unit(self) -> UnitState | None:
        """The unit that gets the next turn in this round, or None if every unit had its turn.

        Players in index order, starting after the last active player (with the first player in a new round);
        a player's units in id order. A unit that cannot act now (energy after its regeneration) is put off
        until no unit that can act is left.
        """
        s: GameState = self._state
        waiting: list[UnitState] = [u for u in s.sorted_units() if u.id not in s.acted_this_round]
        if not waiting:
            return None
        n: int = len(s.players)
        last: int = s.active_player if s.acted_this_round else -1
        waiting.sort(key=lambda u: ((u.owner - last - 1) % n, u.id))
        return next(
            (u for u in waiting if self._can_act(u, self._engine.regenerate(u.stats, u.energy), fresh=True)),
            waiting[0],
        )

    def _begin_turn(self, unit: UnitState) -> list[ev.GameEvent]:
        """Make ``unit`` the active unit and run its turn start: regen -> status ticks -> death (D5)."""
        s: GameState = self._state
        s.active_unit, s.active_player = unit.id, unit.owner
        s.acted_this_round.add(unit.id)
        s.attacks_this_turn = s.actions_this_turn = 0
        return [ev.TurnStarted(unit.owner, s.round, unit.id), *self._start_unit_turn(unit)]

    def _can_act(self, unit: UnitState, energy: int, fresh: bool = False, at: Position | None = None) -> bool:
        """Can the unit move one tile or hit an enemy from where it stands, with ``energy``?

        Every move and every attack (also a move + attack) starts with one of these, so False = nothing to do.
        ``fresh``: as at the start of its turn (no attacks made yet). ``at``: as if it stood on that tile instead.
        """
        board: Board = self._state.board
        position: Position = unit.position if at is None else at
        occupied: set[Position] = self._state.occupied() - {unit.position}
        here: Tile = board.tile(position)
        for step in board.neighbours(position):
            if step not in occupied and self._engine.step_cost(unit.stats, here, board.tile(step)) <= energy:
                return True
        return any(
            self._attack_ready_error(unit, target, energy, fresh) is None
            and self._in_range_from(unit, position, target)
            for target in self._state.units.values()
        )

    def _start_unit_turn(self, unit: UnitState) -> list[ev.GameEvent]:
        """Energy regeneration, then status effect ticks, then death check for one unit."""
        events: list[ev.GameEvent] = []
        before: int = unit.energy
        unit.energy = self._engine.regenerate(unit.stats, unit.energy)
        if unit.energy != before:
            events.append(ev.EnergyRegenerated(unit.id, before, unit.energy))

        tick: TickResult = self._engine.tick_effects(unit.effects, unit.hp, unit.defense)
        hp: int = unit.hp
        for t in tick.ticks:  # one event per stack, each with the HP left after it
            hp -= t.amount
            events.append(ev.EffectTicked(unit.id, t.kind, t.amount, max(0, hp)))
        self._apply_change(unit, tick.hp_change, tick.defense_change, tick.remaining_effects)
        if not unit.alive:
            events += self._kill(unit)
        return events


    def _kill(self, unit: UnitState) -> list[ev.GameEvent]:
        s: GameState = self._state
        events: list[ev.GameEvent] = []
        if unit.id in s.units:
            events.append(self._remove(unit))
        owner: Player = s.players[unit.owner]
        if unit.is_boss and not owner.eliminated:
            owner.eliminated = True
            # D16: the whole army leaves the battlefield
            events += [self._remove(other) for other in s.units_of(unit.owner)]
            events.append(ev.PlayerEliminated(unit.owner))
            alive: list[Player] = s.alive_players()
            if len(alive) <= 1:
                s.phase = Phase.GAME_OVER
                s.winner = alive[0].index if alive else None
                events.append(ev.GameOver(s.winner))
        return events

    # ------------------------------------------------------------------ helpers
    def _remove(self, unit: UnitState) -> ev.UnitDied:
        del self._state.units[unit.id]
        self._state.fallen.append(unit)
        return ev.UnitDied(unit.id, unit.owner, unit.stats.name)

    @staticmethod
    def _apply_change(
        unit: UnitState, hp_change: int, defense_change: int, effects: tuple[StatusEffect, ...]
    ) -> None:
        """Apply an engine result to a unit; DEF never drops below 0."""
        unit.hp += hp_change
        unit.defense = max(0, unit.defense + defense_change)
        unit.effects = effects

    def _apply_move(self, unit: UnitState, info: PathInfo) -> ev.UnitMoved:
        unit.energy -= info.cost
        unit.position = info.path[-1]
        return ev.UnitMoved(unit.id, info.path, info.cost, unit.energy)

    def _resolve(self, attacker: UnitState, target: UnitState, origin: Position | None = None) -> AttackResult:
        """Exact attack outcome; ``origin`` = attack from that tile instead of the attacker's own."""
        return self._engine.resolve_attack(self._snapshot(attacker, origin), self._snapshot(target))

    def _snapshot(self, unit: UnitState, at: Position | None = None) -> CombatantSnapshot:
        position: Position = at if at is not None else unit.position
        tile: Tile = self._state.board.tile(position)
        return CombatantSnapshot(unit.stats, unit.hp, unit.defense, unit.effects, tile, position, unit.id)


    def _own_unit(self, unit_id: int) -> UnitState | None:
        unit, _ = self._own_unit_or_error(unit_id)
        return unit

    def _own_unit_or_error(self, unit_id: int) -> tuple[UnitState | None, str | None]:
        if self._state.phase is not Phase.AWAITING_COMMAND:
            return None, "Game is not accepting commands"
        unit: UnitState | None = self._state.units.get(unit_id)
        if unit is None:
            return None, "Unit does not exist"
        if unit.owner != self._state.active_player:
            return None, "That unit belongs to another player"
        if unit.id != self._state.active_unit:
            return None, "It is not this unit's turn"
        return unit, None

    def _attack_error(self, attacker: UnitState, target: UnitState) -> str | None:
        """Why the attacker cannot hit the target from where it stands now (None = it can)."""
        error: str | None = self._attack_ready_error(attacker, target)
        if error is None and not self._in_range_from(attacker, attacker.position, target):
            return _OUT_OF_RANGE
        return error

    def _plan_attack(
        self,
        attacker: UnitState,
        target: UnitState,
        move_to: Position | None = None,
        origins: dict[Position, PathInfo] | None = None,
    ) -> tuple[AttackPlan | None, str | None]:
        """Check an attack command without changing anything; (plan, None) or (None, reason).

        ``origins``: ``_attack_origins(attacker)``, if already computed.
        """
        error: str | None = self._attack_ready_error(attacker, target)
        if error:
            return None, error
        if move_to is None or move_to == attacker.position:
            if self._in_range_from(attacker, attacker.position, target):  # in range: never move
                result: AttackResult = self._resolve(attacker, target)
                return AttackPlan(attacker.id, target.id, attacker.position, None, result), None
            if move_to is not None or not C.AUTO_MOVE_TO_ATTACK:
                return None, _OUT_OF_RANGE
            options: dict[Position, PathInfo] = origins if origins is not None else self._attack_origins(attacker)
            plans: list[AttackPlan] = [
                self._plan_from(attacker, target, pos, info)
                for pos, info in options.items()
                if self._in_range_from(attacker, pos, target)
            ]
            if not plans:
                return None, _OUT_OF_REACH
            return min(plans, key=lambda plan: self._origin_rank(attacker, plan)), None

        info: PathInfo | None = (origins if origins is not None else self._attack_origins(attacker)).get(move_to)
        if info is None:
            return None, "Not enough energy to move to that tile and attack"
        if not self._in_range_from(attacker, move_to, target):
            return None, "Target is out of range from that tile"
        return self._plan_from(attacker, target, move_to, info), None

    def _plan_from(self, attacker: UnitState, target: UnitState, origin: Position, move: PathInfo) -> AttackPlan:
        return AttackPlan(attacker.id, target.id, origin, move, self._resolve(attacker, target, origin))

    def _origin_rank(self, attacker: UnitState, plan: AttackPlan) -> tuple[int, int, int, int, int]:
        """Lower is better: cheapest move, most damage, best own DEF on the tile, then top-left first."""
        own_defense: int = self._engine.current_defense(attacker.defense, self._state.board.tile(plan.origin))
        return plan.move_cost, -plan.result.damage, -own_defense, plan.origin.y, plan.origin.x

    def _attack_origins(self, attacker: UnitState) -> dict[Position, PathInfo]:
        """Tiles the attacker can move to and still have the energy for one attack."""
        budget: int = attacker.energy - self._engine.attack_cost(attacker.stats)
        if budget < 0:
            return {}
        return self._reachable(attacker, budget)

    def _reachable(self, unit: UnitState, budget: int) -> dict[Position, PathInfo]:
        """Tiles the unit can move to with ``budget`` energy (other units block)."""
        blocked: set[Position] = self._state.occupied() - {unit.position}
        return reachable(self._state.board, self._engine, unit.stats, unit.position, budget, blocked)

    def _in_range_from(self, attacker: UnitState, origin: Position, target: UnitState) -> bool:
        return self._engine.in_attack_range(attacker.stats, origin.manhattan(target.position))

    def _attack_ready_error(
        self, attacker: UnitState, target: UnitState, energy: int | None = None, fresh: bool = False
    ) -> str | None:
        """Checks that do not depend on distance: own unit, energy, attacks left.

        ``energy``: check with this much energy instead of the attacker's own; ``fresh``: no attacks made yet.
        """
        if target.owner == attacker.owner:
            return "Cannot attack your own unit"
        if (attacker.energy if energy is None else energy) < self._engine.attack_cost(attacker.stats):
            return "Not enough energy to attack"
        limit: int | None = C.MAX_ATTACKS_PER_TURN
        if limit is not None and (0 if fresh else self._state.attacks_this_turn) >= limit:
            return "No attacks left this turn"
        return None
