"""The Game State Machine (GSM).

Owns all state changes: validates commands, asks the Calculation Engine for numbers,
applies the results and emits events. It contains no formulas of its own.
"""
from __future__ import annotations

from beastborn.domain.position import Position
from beastborn.domain.unit import UnitState
from beastborn.engine.interface import CalculationEngine
from beastborn.engine.results import AttackResult, CombatantSnapshot
from beastborn.game import events as ev
from beastborn.game.commands import AttackCommand, Command, CommandResult, EndTurnCommand, MoveCommand
from beastborn.game.pathfinding import PathInfo, reachable
from beastborn.game.state import GameState, Phase
from beastborn.game.view import GameView, PlayerView, UnitView


class GameStateMachine:
    def __init__(self, state: GameState, engine: CalculationEngine):
        self._state = state
        self._engine = engine
        self.command_log: list[Command] = []  # with the seed this is enough to replay a game

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

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> list:
        """Begin the first turn. Returns the events of the first turn start."""
        if self._state.phase is not Phase.SETUP:
            raise RuntimeError("Game already started")
        self._state.round = 1
        alive = self._state.alive_players()
        self._state.active_player = alive[0].index
        return self._begin_turn()

    # ------------------------------------------------------------------ queries
    def view(self) -> GameView:
        s = self._state
        units = tuple(
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
            for u in sorted(s.units.values(), key=lambda u: u.id)
        )
        players = tuple(PlayerView(p.index, p.name, p.eliminated) for p in s.players)
        return GameView(s.board, units, players, s.active_player, s.round, s.phase, s.winner, s.seed)

    def reachable_tiles(self, unit_id: int) -> dict[Position, PathInfo]:
        """Where the active player's unit can move this turn (empty if it cannot act)."""
        unit = self._own_unit(unit_id)
        if unit is None:
            return {}
        blocked = self._state.occupied() - {unit.position}
        return reachable(self._state.board, self._engine, unit.stats, unit.position, unit.energy, blocked)

    def preview_attack(self, attacker_id: int, target_id: int) -> AttackResult | None:
        """Exact outcome of an attack (combat has no RNG). Ignores energy and range."""
        attacker = self._state.units.get(attacker_id)
        target = self._state.units.get(target_id)
        if attacker is None or target is None:
            return None
        return self._engine.resolve_attack(self._snapshot(attacker), self._snapshot(target))

    def attack_targets(self, unit_id: int) -> dict[int, AttackResult]:
        """Enemies the active player's unit can attack right now, with the exact outcome."""
        unit = self._own_unit(unit_id)
        if unit is None:
            return {}
        targets = {}
        for target in self._state.units.values():
            if self._attack_error(unit, target) is None:
                targets[target.id] = self.preview_attack(unit.id, target.id)
        return targets

    # ------------------------------------------------------------------ commands
    def submit(self, command: Command) -> CommandResult:
        if self._state.phase is not Phase.AWAITING_COMMAND:
            return CommandResult.rejected(f"Game is not accepting commands (phase: {self._state.phase.value})")
        if isinstance(command, MoveCommand):
            result = self._move(command)
        elif isinstance(command, AttackCommand):
            result = self._attack(command)
        elif isinstance(command, EndTurnCommand):
            result = self._end_turn()
        else:
            return CommandResult.rejected(f"Unknown command: {command!r}")
        if result.ok:
            self.command_log.append(command)
        return result

    def _move(self, cmd: MoveCommand) -> CommandResult:
        unit, error = self._own_unit_or_error(cmd.unit_id)
        if error:
            return CommandResult.rejected(error)
        if not self._state.board.in_bounds(cmd.destination):
            return CommandResult.rejected("Destination is outside the board")
        if cmd.destination in self._state.occupied():
            return CommandResult.rejected("Destination is occupied")
        info = self.reachable_tiles(unit.id).get(cmd.destination)
        if info is None:
            return CommandResult.rejected("Not enough energy to reach that tile")
        unit.energy -= info.cost
        unit.position = cmd.destination
        return CommandResult(True, (ev.UnitMoved(unit.id, info.path, info.cost, unit.energy),))

    def _attack(self, cmd: AttackCommand) -> CommandResult:
        attacker, error = self._own_unit_or_error(cmd.attacker_id)
        if error:
            return CommandResult.rejected(error)
        target = self._state.units.get(cmd.target_id)
        if target is None:
            return CommandResult.rejected("Target does not exist")
        error = self._attack_error(attacker, target)
        if error:
            return CommandResult.rejected(error)

        result = self._engine.resolve_attack(self._snapshot(attacker), self._snapshot(target))
        attacker.energy -= self._engine.attack_cost(attacker.stats)
        self._state.attacks_this_turn[attacker.id] = self._state.attacks_this_turn.get(attacker.id, 0) + 1
        target.hp -= result.damage
        target.defense = max(0, target.defense + result.defense_change)
        target.effects = result.defender_effects_after

        events: list = [ev.UnitAttacked(attacker.id, target.id, result, max(0, target.hp), attacker.energy)]
        if target.hp <= 0:
            events += self._kill(target)
        return CommandResult(True, tuple(events))

    def _end_turn(self) -> CommandResult:
        events: list = [ev.TurnEnded(self._state.active_player)]
        self._advance_player()
        events += self._begin_turn()
        return CommandResult(True, tuple(events))

    # ------------------------------------------------------------------ turn flow
    def _begin_turn(self) -> list:
        """Start-of-turn: regen -> status ticks -> deaths (D5). Skips eliminated players."""
        s = self._state
        events: list = []
        while True:
            player = s.active_player
            s.attacks_this_turn = {}
            events.append(ev.TurnStarted(player, s.round))
            for unit in s.units_of(player):
                if unit.id not in s.units:  # removed earlier in this loop (Boss died)
                    continue
                before = unit.energy
                unit.energy = self._engine.regenerate(unit.stats, unit.energy)
                if unit.energy != before:
                    events.append(ev.EnergyRegenerated(unit.id, before, unit.energy))

                tick = self._engine.tick_effects(unit.effects, unit.hp, unit.defense)
                unit.hp += tick.hp_change
                unit.defense = max(0, unit.defense + tick.defense_change)
                unit.effects = tick.remaining_effects
                for t in tick.ticks:
                    events.append(ev.EffectTicked(unit.id, t.kind, t.amount, max(0, unit.hp)))
                if unit.hp <= 0:
                    events += self._kill(unit)

            if s.phase is Phase.GAME_OVER:
                return events
            if s.players[player].eliminated:
                self._advance_player()
                continue
            s.phase = Phase.AWAITING_COMMAND
            return events

    def _advance_player(self) -> None:
        s = self._state
        n = len(s.players)
        for step in range(1, n + 1):
            candidate = (s.active_player + step) % n
            if not s.players[candidate].eliminated:
                if candidate <= s.active_player:
                    s.round += 1
                s.active_player = candidate
                return
        raise RuntimeError("No players left")

    def _kill(self, unit: UnitState) -> list:
        s = self._state
        events: list = []
        if unit.id in s.units:
            del s.units[unit.id]
            events.append(ev.UnitDied(unit.id, unit.owner, unit.stats.name))
        if unit.is_boss and not s.players[unit.owner].eliminated:
            s.players[unit.owner].eliminated = True
            for other in s.units_of(unit.owner):  # D16: the whole army leaves the battlefield
                del s.units[other.id]
                events.append(ev.UnitDied(other.id, other.owner, other.stats.name))
            events.append(ev.PlayerEliminated(unit.owner))
            alive = s.alive_players()
            if len(alive) <= 1:
                s.phase = Phase.GAME_OVER
                s.winner = alive[0].index if alive else None
                events.append(ev.GameOver(s.winner))
        return events

    # ------------------------------------------------------------------ helpers
    def _snapshot(self, unit: UnitState) -> CombatantSnapshot:
        tile = self._state.board.tile(unit.position)
        return CombatantSnapshot(unit.stats, unit.hp, unit.defense, unit.effects, tile, unit.position)

    def _own_unit(self, unit_id: int) -> UnitState | None:
        unit, error = self._own_unit_or_error(unit_id)
        return None if error else unit

    def _own_unit_or_error(self, unit_id: int) -> tuple[UnitState | None, str | None]:
        if self._state.phase is not Phase.AWAITING_COMMAND:
            return None, "Game is not accepting commands"
        unit = self._state.units.get(unit_id)
        if unit is None:
            return None, "Unit does not exist"
        if unit.owner != self._state.active_player:
            return None, "That unit belongs to another player"
        return unit, None

    def _attack_error(self, attacker: UnitState, target: UnitState) -> str | None:
        if target.owner == attacker.owner:
            return "Cannot attack your own unit"
        distance = attacker.position.manhattan(target.position)
        if not self._engine.in_attack_range(attacker.stats, distance):
            return "Target is out of range"
        if attacker.energy < self._engine.attack_cost(attacker.stats):
            return "Not enough energy to attack"
        limit = self._state.config.max_attacks_per_turn
        if limit is not None and self._state.attacks_this_turn.get(attacker.id, 0) >= limit:
            return "No attacks left this turn"
        return None
