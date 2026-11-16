"""Beastborn Game Notation (BGN): a whole game in one line of text, enough to replay it command by command.

    [1793637382469][42][10x10][2][Learner|Opponent][a1:(b2,-) j10:(xh9,-) b2:c3 c3:xd4 c3:-]

Fields, each in brackets:

1. game id: milliseconds since the Unix epoch when the game was created,
2. map seed (``new_game(seed=...)``),
3. board size, width ``x`` height,
4. number of players,
5. player names, ``|`` between them (``%``, ``[``, ``]``, ``|`` and line breaks are written as ``%25``, ``%5B``...),
6. game data.

A ``.bgn`` file (UTF-8) holds any number of games, one per line. Seed, size and players are all ``new_game`` needs to
build the same map and armies again (with the same units.json and constant.py).

Tiles are written as in chess: column letter + row number, ``a1`` = ``Position(0, 0)`` (top-left), x -> ``a..p``,
y -> ``1..16``.

Game data: one entry per command of ``GameStateMachine.command_log``, separated by spaces. An entry is the tile the
active unit stands on when the command starts, ``:`` and the command:

- ``c3``: ``MoveCommand`` to c3,
- ``xd4``: ``AttackCommand`` on the unit on d4 from where the unit stands,
- ``c3xd4``: ``AttackCommand`` on the unit on d4, moving to c3 first (``move_to``; also the unit's own tile, as
  logged),
- ``-``: ``EndTurnCommand`` (the unit waits),
- ``(c3xd4,xe5,-)``: a ``WrapperCommand`` of these commands (a bot plays a whole unit turn as one command).

The unit that acts is always the GSM's active unit, so the tile before ``:`` only checks that the replay still
follows the game. Turning an entry back into a command (``BgnEntry.to_command``) needs the game in the state
before it, to find the target units on their tiles.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from beastborn.domain.position import Position
from beastborn.engine.interface import CalculationEngine
from beastborn.game import events as ev
from beastborn.game.commands import (
    AttackCommand, Command, CommandResult, CommandType, EndTurnCommand, MoveCommand, WrapperCommand,
)
from beastborn.game.setup import new_game
from beastborn.game.state import Phase
from beastborn.game.state_machine import GameStateMachine
from beastborn.game.view import GameView, UnitView

COLUMNS: str = "abcdefghijklmnop"  # column letters: a board has at most this many columns and rows
END_TURN: str = "-"
ATTACK: str = "x"
NAME_SEPARATOR: str = "|"
ENTRY_SEPARATOR: str = " "

_TILE = re.compile(r"([a-p])([1-9][0-9]*)")
_LINE = re.compile(r"\[(-?\d+)\]\[(-?\d+)\]\[(\d+)x(\d+)\]\[(\d+)\]\[([^\[\]]*)\]\[([^\[\]]*)\]")
_ESCAPED: tuple[str, ...] = ("%", "[", "]", NAME_SEPARATOR, "\n", "\r")  # "%" first: it starts every escape
_ESCAPE = re.compile(r"%([0-9A-Fa-f]{2})")


class BgnError(ValueError):
    """Text that is not valid BGN, or a record that does not fit the game it is replayed in."""


# ---------------------------------------------------------------------- tiles
def tile_name(pos: Position) -> str:
    """``Position(2, 3)`` -> ``"c4"``."""
    if not (0 <= pos.x < len(COLUMNS) and 0 <= pos.y < len(COLUMNS)):
        raise BgnError(f"Position {pos} has no BGN tile name (a1..p16)")
    return f"{COLUMNS[pos.x]}{pos.y + 1}"


def parse_tile(text: str) -> Position:
    """``"c4"`` -> ``Position(2, 3)``."""
    match: re.Match[str] | None = _TILE.fullmatch(text)
    if match is None or int(match[2]) > len(COLUMNS):
        raise BgnError(f"Not a tile: {text!r} (a1..p16)")
    return Position(COLUMNS.index(match[1]), int(match[2]) - 1)


# ---------------------------------------------------------------------- commands
@dataclass(frozen=True)
class BgnAction:
    """One plain command in notation. The acting unit is not in it: it is always the active unit."""

    kind: CommandType  # MOVE, ATTACK or END_TURN
    tile: Position | None = None  # MOVE: the destination; ATTACK: the target's tile
    move_to: Position | None = None  # ATTACK: ``AttackCommand.move_to``

    def __post_init__(self) -> None:
        needs_tile: bool = self.kind in (CommandType.MOVE, CommandType.ATTACK)
        if self.kind is CommandType.WRAPPER or needs_tile != (self.tile is not None) or (
            self.move_to is not None and self.kind is not CommandType.ATTACK
        ):
            raise BgnError(f"Not a BGN action: {self.kind.value} {self.tile} {self.move_to}")

    def __str__(self) -> str:
        if self.kind is CommandType.END_TURN:
            return END_TURN
        assert self.tile is not None, "checked in __post_init__"
        if self.kind is CommandType.MOVE:
            return tile_name(self.tile)
        return f"{tile_name(self.move_to) if self.move_to else ''}{ATTACK}{tile_name(self.tile)}"

    @classmethod
    def parse(cls, text: str) -> BgnAction:
        if text == END_TURN:
            return cls(CommandType.END_TURN)
        if ATTACK in text:
            move_to, target = text.split(ATTACK, 1)
            return cls(CommandType.ATTACK, parse_tile(target), parse_tile(move_to) if move_to else None)
        return cls(CommandType.MOVE, parse_tile(text))

    def to_command(self, view: GameView, unit: UnitView) -> Command:
        """The command for ``unit`` (the active unit) in the game ``view`` shows."""
        if self.kind is CommandType.END_TURN:
            return EndTurnCommand()
        assert self.tile is not None, "checked in __post_init__"
        if self.kind is CommandType.MOVE:
            return MoveCommand(unit.id, self.tile)
        target: UnitView | None = view.unit_at(self.tile)
        if target is None:
            raise BgnError(f"No unit on {tile_name(self.tile)} to attack")
        return AttackCommand(unit.id, target.id, self.move_to)


@dataclass(frozen=True)
class BgnEntry:
    """One command of the command log: where the active unit stood when it started, and what it did.

    ``wrapped``: the actions form one ``WrapperCommand`` (also a single one), otherwise ``actions`` is one command.
    """

    origin: Position
    actions: tuple[BgnAction, ...]
    wrapped: bool = False

    def __post_init__(self) -> None:
        if not self.actions or (len(self.actions) > 1 and not self.wrapped):
            raise BgnError("An entry is one command, or a wrapper of at least one")

    def __str__(self) -> str:
        body: str = ",".join(str(action) for action in self.actions)
        return f"{tile_name(self.origin)}:{f'({body})' if self.wrapped else body}"

    @classmethod
    def parse(cls, text: str) -> BgnEntry:
        origin, colon, body = text.partition(":")
        if not colon or not body:
            raise BgnError(f"Not a BGN entry: {text!r}")
        wrapped: bool = body.startswith("(") and body.endswith(")")
        parts: list[str] = body[1:-1].split(",") if wrapped else [body]
        return cls(parse_tile(origin), tuple(BgnAction.parse(part) for part in parts), wrapped)

    @classmethod
    def from_command(cls, command: Command, origin: Position, events: Sequence[ev.GameEvent]) -> BgnEntry:
        """A logged command (as ``GameListener.on_command`` gets it) with the events of its ``CommandResult``.

        The attacks' target tiles come from the ``UnitAttacked`` events: one per attack, in order.
        """
        wrapped: bool = isinstance(command, WrapperCommand)
        parts: tuple[Command, ...] = command.commands if isinstance(command, WrapperCommand) else (command,)
        targets = iter([event.target for event in events if isinstance(event, ev.UnitAttacked)])
        actions: list[BgnAction] = []
        for part in parts:
            if isinstance(part, MoveCommand):
                actions.append(BgnAction(CommandType.MOVE, part.destination))
            elif isinstance(part, AttackCommand):
                target: Position | None = next(targets, None)
                if target is None:
                    raise BgnError(f"No attack event for {part!r}")
                actions.append(BgnAction(CommandType.ATTACK, target, part.move_to))
            elif isinstance(part, EndTurnCommand):
                actions.append(BgnAction(CommandType.END_TURN))
            else:
                raise BgnError(f"Command {part!r} has no BGN notation")
        return cls(origin, tuple(actions), wrapped)

    def to_command(self, gsm: GameStateMachine) -> Command:
        """The command this entry stands for, in ``gsm`` as it is now (the state before the entry).

        Raises ``BgnError`` when the game does not fit: it is over, or the active unit is not on ``origin``.
        """
        view: GameView = gsm.view()
        unit: UnitView | None = view.unit(view.active_unit) if view.active_unit is not None else None
        if view.phase is not Phase.AWAITING_COMMAND or unit is None:
            raise BgnError(f"{self}: the game is over")
        if unit.position != self.origin:
            raise BgnError(f"{self}: the active unit stands on {tile_name(unit.position)}, "
                           f"not on {tile_name(self.origin)}")
        commands: tuple[Command, ...] = tuple(action.to_command(view, unit) for action in self.actions)
        return WrapperCommand(commands) if self.wrapped else commands[0]


# ---------------------------------------------------------------------- games
@dataclass(frozen=True)
class GameRecord:
    """One game in BGN: what ``new_game`` needs to build it, and its command log as entries."""

    game_id: int  # milliseconds since the Unix epoch when the game was created
    seed: int
    width: int
    height: int
    player_names: tuple[str, ...]
    entries: tuple[BgnEntry, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "player_names", tuple(self.player_names))
        object.__setattr__(self, "entries", tuple(self.entries))
        if not (1 <= self.width <= len(COLUMNS) and 1 <= self.height <= len(COLUMNS)):
            raise BgnError(f"Board {self.width}x{self.height}: BGN tiles go up to {len(COLUMNS)}x{len(COLUMNS)}")
        if not self.player_names:
            raise BgnError("A game needs players")

    @property
    def num_players(self) -> int:
        return len(self.player_names)

    def new_game(self, engine: CalculationEngine | None = None) -> GameStateMachine:
        """The game as it started: same map, armies and player names, no command played yet."""
        return new_game(self.num_players, self.seed, self.width, self.height, engine=engine,
                        player_names=list(self.player_names))

    def to_line(self) -> str:
        names: str = NAME_SEPARATOR.join(_escape(name) for name in self.player_names)
        data: str = ENTRY_SEPARATOR.join(str(entry) for entry in self.entries)
        return f"[{self.game_id}][{self.seed}][{self.width}x{self.height}][{self.num_players}][{names}][{data}]"

    @classmethod
    def from_line(cls, line: str) -> GameRecord:
        match: re.Match[str] | None = _LINE.fullmatch(line.strip())
        if match is None:
            raise BgnError("Not a BGN game: expected [id][seed][WxH][players][names][data]")
        game_id, seed, width, height, players, names, data = match.groups()
        player_names: tuple[str, ...] = tuple(_unescape(name) for name in names.split(NAME_SEPARATOR))
        if len(player_names) != int(players):
            raise BgnError(f"{players} players but {len(player_names)} names")
        entries: tuple[BgnEntry, ...] = tuple(BgnEntry.parse(text) for text in data.split())
        return cls(int(game_id), int(seed), int(width), int(height), player_names, entries)


def replay(record: GameRecord, engine: CalculationEngine | None = None) -> GameStateMachine:
    """The recorded game played to its last entry. Raises ``BgnError`` if an entry does not fit the game."""
    gsm: GameStateMachine = record.new_game(engine)
    for number, entry in enumerate(record.entries, start=1):
        result: CommandResult = gsm.submit(entry.to_command(gsm))
        if not result.ok:
            raise BgnError(f"Entry {number} ({entry}) was rejected: {result.error}")
    return gsm


# ---------------------------------------------------------------------- files
def parse_bgn(text: str) -> list[GameRecord]:
    """Every game in BGN text (one per line, empty lines skipped)."""
    records: list[GameRecord] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if line.strip():
            try:
                records.append(GameRecord.from_line(line))
            except BgnError as error:
                raise BgnError(f"Line {number}: {error}") from error
    return records


def read_bgn(path: Path | str) -> list[GameRecord]:
    return parse_bgn(Path(path).read_text(encoding="utf-8"))


def write_bgn(path: Path | str, records: Iterable[GameRecord]) -> None:
    """A new file with these games (an existing one is replaced)."""
    Path(path).write_text("".join(record.to_line() + "\n" for record in records), encoding="utf-8", newline="\n")


def append_bgn(path: Path | str, record: GameRecord) -> None:
    """Add one game at the end of the file (the file is created if needed)."""
    with open(path, "a", encoding="utf-8", newline="\n") as file:
        file.write(record.to_line() + "\n")


def _escape(name: str) -> str:
    for char in _ESCAPED:
        name = name.replace(char, f"%{ord(char):02X}")
    return name


def _unescape(text: str) -> str:
    return _ESCAPE.sub(lambda match: chr(int(match[1], 16)), text)
