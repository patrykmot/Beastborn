"""Web input: JSON request -> the existing UI intents from ui/interface.py."""
from __future__ import annotations

from beastborn.domain.position import Position
from beastborn.ui.interface import Cancel, ClickTile, EndTurn, UIIntent
from beastborn.ui.web.schemas import IntentRequest


class InvalidIntent(ValueError):
    pass


def to_intent(request: IntentRequest, board_width: int, board_height: int) -> UIIntent:
    if request.type == "end_turn":
        return EndTurn()
    if request.type == "cancel":
        return Cancel()
    # click
    if request.x is None or request.y is None:
        raise InvalidIntent("click needs x and y")
    position: Position = Position(request.x, request.y)
    if not position.inside(board_width, board_height):
        raise InvalidIntent(f"{position} is outside the {board_width}x{board_height} board")
    return ClickTile(position)
