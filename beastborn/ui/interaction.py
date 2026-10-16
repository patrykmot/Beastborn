"""Frontend-neutral presenter: turns intents into GSM commands for the active unit (the GSM picks it)."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from beastborn.constant import LOG_HISTORY
from beastborn.control.controller import HumanController, PlayerController
from beastborn.domain.position import Position
from beastborn.engine.results import AttackResult
from beastborn.game import events as ev
from beastborn.game.attack_plan import AttackPlan
from beastborn.game.commands import AttackCommand, Command, CommandResult, EndTurnCommand, MoveCommand
from beastborn.game.pathfinding import PathInfo
from beastborn.game.state import Phase
from beastborn.game.state_machine import GameStateMachine
from beastborn.game.view import GameView, UnitView
from beastborn.ui.interface import ClickTile, EndTurn, HoverTile, Quit, UIIntent
from beastborn.ui.text import describe, round_header, unit_label


@dataclass(frozen=True)
class MoveAnimation:
    """What a frontend needs to animate one move: the unit walks ``path`` (start and destination included)."""

    seq: int  # number of the command that made the move
    unit_id: int
    owner: int
    path: tuple[Position, ...]


@dataclass(frozen=True)
class AttackAnimation:
    """What a frontend needs to animate one attack. Tiles are taken before the hit (a killed target is gone after)."""

    seq: int  # number of the command that made the attack
    attacker_id: int
    target_id: int
    attacker_type: str
    owner: int
    source: Position  # where the attacker hit from
    target: Position
    ranged: bool
    damage: int
    killed: bool


class Interaction:
    def __init__(self, gsm: GameStateMachine, controllers: dict[int, PlayerController] | None = None):
        self.gsm: GameStateMachine = gsm
        self.controllers: dict[int, PlayerController] = controllers or {}
        self.hovered: Position | None = None
        self.message: str = ""
        self.message_is_error: bool = False
        self.log: deque[str] = deque(maxlen=LOG_HISTORY)
        self.action_seq: int = 0  # number of accepted commands so far
        # moves and attacks since the last player input (incl. bot turns), in the order they happened
        self.last_actions: list[MoveAnimation | AttackAnimation] = []
        self.quit_requested: bool = False
        self._cache_key: int | None = None  # accepted commands when the derived state below was computed
        self._view: GameView = gsm.view()
        self._reachable: dict[Position, PathInfo] = {}
        self._plans: dict[int, AttackPlan] = {}
        self._score: int = gsm.score(self._view.active_player)
        self.log.append(f"Seed {self._view.seed}" if self._view.seed is not None else "Custom game")
        self.log.append(round_header(self._view.round))

    # ------------------------------------------------------------------ derived state
    def _refresh(self) -> None:
        key: int = len(self.gsm.command_log)
        if key == self._cache_key:
            return
        self._cache_key = key
        self._view = self.gsm.view()
        self._score = self.gsm.score(self._view.active_player)
        active: int | None = self._view.active_unit
        if active is None or self._view.phase is Phase.GAME_OVER:
            self._reachable, self._plans = {}, {}
        else:
            self._reachable = self.gsm.reachable_tiles(active)
            self._plans = self.gsm.attack_plans(active)

    @property
    def view(self) -> GameView:
        self._refresh()
        return self._view

    @property
    def reachable(self) -> dict[Position, PathInfo]:
        """Where the active unit can move."""
        self._refresh()
        return self._reachable

    @property
    def attack_plans(self) -> dict[int, AttackPlan]:
        """Every enemy a click can attack, including those the active unit first has to move to."""
        self._refresh()
        return self._plans

    @property
    def score(self) -> int:
        """Board score of the active player, refreshed after every accepted command."""
        self._refresh()
        return self._score

    @property
    def active_unit(self) -> UnitView | None:
        """The unit whose turn it is (None after the game is over)."""
        view: GameView = self.view
        if view.phase is Phase.GAME_OVER or view.active_unit is None:
            return None
        return view.unit(view.active_unit)

    @property
    def hovered_unit(self) -> UnitView | None:
        return self.view.unit_at(self.hovered) if self.hovered is not None else None

    def hover_path(self) -> PathInfo | None:
        return self.reachable.get(self.hovered) if self.hovered is not None else None

    def hover_plan(self) -> AttackPlan | None:
        """What a click on the hovered enemy would do (None if it would be rejected)."""
        target: UnitView | None = self.hovered_unit
        return self.attack_plans.get(target.id) if target is not None else None

    def hover_attack(self) -> AttackResult | None:
        """Exact attack outcome if the active unit attacks the hovered enemy.

        From the tile it would move to first, if any; otherwise from where it stands (even if out of range).
        """
        active: UnitView | None = self.active_unit
        target: UnitView | None = self.hovered_unit
        if active is None or target is None or target.owner == active.owner:
            return None
        plan: AttackPlan | None = self.hover_plan()
        return plan.result if plan is not None else self.gsm.preview_attack(active.id, target.id)

    @property
    def human_turn(self) -> bool:
        controller: PlayerController = self.controllers.get(self.view.active_player, HumanController())
        return controller.is_human

    @property
    def _waiting_for_human(self) -> bool:
        return self.view.phase is not Phase.GAME_OVER and self.human_turn

    # ------------------------------------------------------------------ input
    def handle(self, intent: UIIntent) -> None:
        if not isinstance(intent, HoverTile):
            self.last_actions = []  # the frontend has shown them with the previous response
        if isinstance(intent, Quit):
            self.quit_requested = True
        elif isinstance(intent, HoverTile):
            self.hovered = intent.position
        elif not self._waiting_for_human:
            return
        elif isinstance(intent, EndTurn):
            self.submit(EndTurnCommand())
        elif isinstance(intent, ClickTile):
            self._click(intent.position)

    def _click(self, pos: Position) -> None:
        """Enemy = attack it (moving first if needed), reachable tile = move there. Units cannot be chosen."""
        active: UnitView | None = self.active_unit
        clicked: UnitView | None = self.view.unit_at(pos)
        if active is None:
            return
        if clicked is not None and clicked.owner != active.owner:
            self.submit(AttackCommand(active.id, clicked.id))
        elif clicked is None and pos in self.reachable:
            self.submit(MoveCommand(active.id, pos))
        elif clicked is not None and clicked.id != active.id:
            self._say(f"Units act in turn: now it is {unit_label(self.view, active.id)}", error=True)
        else:
            self._say("")

    def tick(self) -> None:
        """Let non-human controllers (bots) act. Call once per frame."""
        if self.view.phase is Phase.GAME_OVER or self.human_turn:
            return
        command: Command | None = self.controllers[self.view.active_player].choose_command(self.gsm)
        if command is not None:
            self.submit(command)

    def submit(self, command: Command) -> bool:
        before: GameView = self.gsm.view()
        result: CommandResult = self.gsm.submit(command)
        if result.events:  # a WrapperCommand rejected part-way keeps what it already did
            self._record(result.events, before)
        if not result.ok:
            self._say(result.error or "Not allowed", error=True)
            return False
        self._say("")
        return True

    def _record(self, events: tuple[ev.GameEvent, ...], before: GameView) -> None:
        """Log lines and animations for the events of one accepted command."""
        after: GameView = self.gsm.view()
        self.action_seq += 1
        for event in events:
            if isinstance(event, ev.UnitMoved):
                owner: int = after.unit(event.unit_id).owner  # a move never kills, so the unit is still there
                self.last_actions.append(MoveAnimation(self.action_seq, event.unit_id, owner, event.path))
            if isinstance(event, ev.UnitAttacked):
                self.last_actions.append(self._animation(event, before))
            line: str | None = describe(event, before, after)
            if line:
                self.log.append(line)

    def _animation(self, event: ev.UnitAttacked, before: GameView) -> AttackAnimation:
        attacker: UnitView | None = before.unit(event.attacker_id)
        target: UnitView | None = before.unit(event.target_id)
        assert attacker is not None and target is not None, "an attack always involves two existing units"
        return AttackAnimation(
            seq=self.action_seq,
            attacker_id=attacker.id,
            target_id=target.id,
            attacker_type=attacker.stats.key,
            owner=attacker.owner,
            source=event.source,  # the attacker may have moved first, so not its tile in ``before``
            target=event.target,
            ranged=event.result.ranged,
            damage=event.result.damage,
            killed=event.target_hp_after <= 0,
        )


    def _say(self, text: str, error: bool = False) -> None:
        self.message, self.message_is_error = text, error
