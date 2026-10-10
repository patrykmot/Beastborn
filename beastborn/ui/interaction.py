"""Frontend-neutral presenter: selection state + turning intents into GSM commands."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from beastborn.constance import LOG_HISTORY
from beastborn.control.controller import HumanController, PlayerController
from beastborn.domain.position import Position
from beastborn.engine.results import AttackResult
from beastborn.game import events as ev
from beastborn.game.commands import AttackCommand, Command, CommandResult, EndTurnCommand, MoveCommand
from beastborn.game.pathfinding import PathInfo
from beastborn.game.state import Phase
from beastborn.game.state_machine import GameStateMachine
from beastborn.game.view import GameView, UnitView
from beastborn.ui.interface import Cancel, ClickTile, EndTurn, HoverTile, Quit, UIIntent
from beastborn.ui.text import describe, round_header

CacheKey = tuple[int, int | None]  # (accepted commands, selected unit id)


@dataclass(frozen=True)
class AttackAnimation:
    """What a frontend needs to animate one attack. Tiles are taken before the hit (a killed target is gone after)."""

    seq: int  # number of the command that made the attack
    attacker_id: int
    target_id: int
    attacker_type: str
    owner: int
    source: Position
    target: Position
    ranged: bool
    damage: int
    killed: bool


class Interaction:
    def __init__(self, gsm: GameStateMachine, controllers: dict[int, PlayerController] | None = None):
        self.gsm: GameStateMachine = gsm
        self.controllers: dict[int, PlayerController] = controllers or {}
        self.selected: int | None = None
        self.hovered: Position | None = None
        self.message: str = ""
        self.message_is_error: bool = False
        self.log: deque[str] = deque(maxlen=LOG_HISTORY)
        self.action_seq: int = 0  # number of accepted commands so far
        self.last_attacks: list[AttackAnimation] = []  # attacks since the last player input (incl. bot turns)
        self.quit_requested: bool = False
        self._cache_key: CacheKey | None = None
        self._view: GameView = gsm.view()
        self._reachable: dict[Position, PathInfo] = {}
        self._targets: dict[int, AttackResult] = {}
        self.log.append(f"Seed {self._view.seed}" if self._view.seed is not None else "Custom game")
        self.log.append(round_header(self._view.round, self._view.players[self._view.active_player].name))

    # ------------------------------------------------------------------ derived state
    def _refresh(self) -> None:
        key: CacheKey = (len(self.gsm.command_log), self.selected)
        if key == self._cache_key:
            return
        self._cache_key = key
        self._view = self.gsm.view()
        if self.selected is not None and self._view.unit(self.selected) is None:
            self.selected = None
        if self.selected is None:
            self._reachable, self._targets = {}, {}
        else:
            self._reachable = self.gsm.reachable_tiles(self.selected)
            self._targets = self.gsm.attack_targets(self.selected)

    @property
    def view(self) -> GameView:
        self._refresh()
        return self._view

    @property
    def reachable(self) -> dict[Position, PathInfo]:
        self._refresh()
        return self._reachable

    @property
    def targets(self) -> dict[int, AttackResult]:
        self._refresh()
        return self._targets

    @property
    def selected_unit(self) -> UnitView | None:
        return self.view.unit(self.selected) if self.selected is not None else None

    @property
    def hovered_unit(self) -> UnitView | None:
        return self.view.unit_at(self.hovered) if self.hovered is not None else None

    def hover_path(self) -> PathInfo | None:
        return self.reachable.get(self.hovered) if self.hovered is not None else None

    def hover_attack(self) -> AttackResult | None:
        """Exact attack outcome if the selected unit attacks the hovered enemy (even if out of range)."""
        target: UnitView | None = self.hovered_unit
        if self.selected is None or target is None or target.owner == self.view.active_player:
            return None
        return self.gsm.preview_attack(self.selected, target.id)

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
            self.last_attacks = []  # the frontend has shown them with the previous response
        if isinstance(intent, Quit):
            self.quit_requested = True
        elif isinstance(intent, HoverTile):
            self.hovered = intent.position
        elif isinstance(intent, Cancel):
            self._deselect()
        elif not self._waiting_for_human:
            return
        elif isinstance(intent, EndTurn):
            self.selected = None
            self.submit(EndTurnCommand())
        elif isinstance(intent, ClickTile):
            self._click(intent.position)

    def _click(self, pos: Position) -> None:
        view: GameView = self.view
        clicked: UnitView | None = view.unit_at(pos)

        if clicked is not None and clicked.owner == view.active_player:
            self.selected = None if clicked.id == self.selected else clicked.id
            self._say("")
        elif self.selected is None:
            if clicked is not None:
                self._say(f"That {clicked.name} belongs to {view.players[clicked.owner].name}", error=True)
        elif clicked is not None:
            self.submit(AttackCommand(self.selected, clicked.id))
        elif pos in self.reachable:
            self.submit(MoveCommand(self.selected, pos))
        else:
            self._deselect()

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
        if not result.ok:
            self._say(result.error or "Not allowed", error=True)
            return False
        after: GameView = self.gsm.view()
        self.action_seq += 1
        for event in result.events:
            if isinstance(event, ev.UnitAttacked):
                self.last_attacks.append(self._animation(event, before))
            line: str | None = describe(event, before, after)
            if line:
                self.log.append(line)
        self._say("")
        if isinstance(command, EndTurnCommand):
            self.selected = None
        return True

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
            source=attacker.position,
            target=target.position,
            ranged=event.result.ranged,
            damage=event.result.damage,
            killed=event.target_hp_after <= 0,
        )

    def _deselect(self) -> None:
        self.selected = None
        self._say("")

    def _say(self, text: str, error: bool = False) -> None:
        self.message, self.message_is_error = text, error
