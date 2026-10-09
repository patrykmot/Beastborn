"""Frontend-neutral presenter: selection state + turning intents into GSM commands."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from beastborn.control.controller import HumanController, PlayerController
from beastborn.domain.position import Position
from beastborn.engine.results import AttackResult
from beastborn.game.commands import AttackCommand, Command, EndTurnCommand, MoveCommand
from beastborn.game.pathfinding import PathInfo
from beastborn.game.state import Phase
from beastborn.game.state_machine import GameStateMachine
from beastborn.game.view import GameView, UnitView
from beastborn.ui.interface import Cancel, ClickTile, EndTurn, HoverTile, Quit, UIIntent
from beastborn.ui.text import describe

RECENT_ACTIONS = 20


@dataclass(frozen=True)
class ActionRecord:
    """One accepted command: its events and the view from just before it (for animations)."""

    seq: int
    events: tuple
    before: GameView


class Interaction:
    def __init__(self, gsm: GameStateMachine, controllers: dict[int, PlayerController] | None = None):
        self.gsm = gsm
        self.controllers = controllers or {}
        self.selected: int | None = None
        self.hovered: Position | None = None
        self.message: str = ""
        self.message_is_error = False
        self.log: deque[str] = deque(maxlen=200)
        self.action_seq = 0  # number of accepted commands so far
        self.actions: deque[ActionRecord] = deque(maxlen=RECENT_ACTIONS)
        self.quit_requested = False
        self._cache_key: tuple | None = None
        self._view: GameView = gsm.view()
        self._reachable: dict[Position, PathInfo] = {}
        self._targets: dict[int, AttackResult] = {}
        self.log.append(f"Seed {self._view.seed}" if self._view.seed is not None else "Custom game")
        self.log.append(f"--- Round 1: {self._view.players[self._view.active_player].name} ---")

    # ------------------------------------------------------------------ derived state
    def _refresh(self) -> None:
        key = (len(self.gsm.command_log), self.selected)
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
        target = self.hovered_unit
        if self.selected is None or target is None or target.owner == self.view.active_player:
            return None
        return self.gsm.preview_attack(self.selected, target.id)

    @property
    def human_turn(self) -> bool:
        controller = self.controllers.get(self.view.active_player, HumanController())
        return controller.is_human

    # ------------------------------------------------------------------ input
    def handle(self, intent: UIIntent) -> None:
        if isinstance(intent, Quit):
            self.quit_requested = True
        elif isinstance(intent, HoverTile):
            self.hovered = intent.position
        elif isinstance(intent, Cancel):
            self.selected = None
            self._say("")
        elif self.view.phase is Phase.GAME_OVER or not self.human_turn:
            return
        elif isinstance(intent, EndTurn):
            self.selected = None
            self.submit(EndTurnCommand())
        elif isinstance(intent, ClickTile):
            self._click(intent.position)

    def _click(self, pos: Position) -> None:
        view = self.view
        clicked = view.unit_at(pos)
        own = clicked is not None and clicked.owner == view.active_player

        if own:
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
            self.selected = None
            self._say("")

    def tick(self) -> None:
        """Let non-human controllers (bots) act. Call once per frame."""
        if self.view.phase is Phase.GAME_OVER or self.human_turn:
            return
        command = self.controllers[self.view.active_player].choose_command(self.gsm)
        if command is not None:
            self.submit(command)

    def submit(self, command: Command) -> bool:
        before = self.gsm.view()
        result = self.gsm.submit(command)
        if not result.ok:
            self._say(result.error or "Not allowed", error=True)
            return False
        after = self.gsm.view()
        self.action_seq += 1
        self.actions.append(ActionRecord(self.action_seq, tuple(result.events), before))
        for event in result.events:
            line = describe(event, before, after)
            if line:
                self.log.append(line)
        self._say("")
        if isinstance(command, EndTurnCommand):
            self.selected = None
        return True

    def _say(self, text: str, error: bool = False) -> None:
        self.message, self.message_is_error = text, error
