from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import List

BOARD_ROWS = 20
BOARD_ROWS_LAST = BOARD_ROWS - 1
BOARD_COLUMNS: int = 20
BOARD_COLUMNS_LAST = BOARD_COLUMNS - 1


class GameState(Enum):
    INIT = 1
    PLAY = 2
    END = 3

class Terrain(Enum):
    FOREST = 1
    DESERT = 2
    SNOW = 3
    FLAT = 4

@dataclass
class Beast:
    health: float
    defence: float
    # Min points of attack
    attack_min: float
    # Max points of attack
    attack_max: float
    #Speed of movement
    speed: float
    # Where beast was born, and feels better!
    origin: Terrain
    # Chance to get min attack power from other Beast
    luck: float
    name: str



@dataclass
class BoardGround:
    beast: Beast = None
    terrain: Terrain = None


@dataclass
class BoardParameters:
    players_terrain: List[Terrain]

@dataclass
class BoardPosition:
    x: int
    y: int

    def __init__(self, x: int, y: int):
        self.x = x
        self.y = y
        if x < 0 or x >= BOARD_ROWS:
            raise Exception(f"Bad position x = {x}")
        if y < 0 or y >= BOARD_COLUMNS:
            raise Exception(f"Bad position y = {y}")


class Board:
    def __init__(self, p: BoardParameters):


        # Put players in corner
        if len(p.players_terrain) > 4 or len(p.players_terrain) < 2:
            raise Exception("Bad number of players!")

        # Players starting position
        self.psp: List[BoardPosition] = list()
        self.psp.append(BoardPosition(0,0))
        self.psp.append(BoardPosition(BOARD_ROWS_LAST, 0))
        if  len(p.players_terrain) > 2:
            self.psp.append(BoardPosition(BOARD_ROWS_LAST, BOARD_COLUMNS_LAST))
        if  len(p.players_terrain) > 3:
            self.psp.append(BoardPosition(0, BOARD_COLUMNS_LAST))

        # Generate terrain
        # Generate array
        self._board: List[List[BoardGround]]
        self._board = [[0 for _ in range(BOARD_COLUMNS)] for _ in range(BOARD_ROWS)]
        for row in range(0, BOARD_ROWS):
            for col in range(0, BOARD_COLUMNS):
                # Create BoardGround
                bg = BoardGround()
                bg.terrain = Terrain.FLAT
                self._board[row][col] = bg

        # Generate train around players
        for (pos,terrain) in zip(self.psp, p.players_terrain):
            self.generate_terrain(pos, terrain, 5)



    def generate_terrain(self, pos: BoardPosition , terrain: Terrain, size: int):
        min_x: int = pos.x - size if pos.x - size >= 0 else 0
        max_x: int = pos.x + size if pos.x + size <= BOARD_ROWS else BOARD_ROWS
        min_y: int = pos.y - size if pos.y - size >= 0 else 0
        max_y: int = pos.y + size if pos.y + size <= BOARD_COLUMNS else BOARD_COLUMNS

        for row in range(min_x, max_x):
            for col in range(min_y, max_y):
                self._board[row][col].terrain = terrain

    def __str__(self):
        tmp: str = ""
        for row in self._board:
            bg: BoardGround
            for bg in row:
                if bg.terrain is not None:
                    if Terrain.FLAT == bg.terrain:
                        tmp += '.'
                    else:
                        tmp += bg.terrain.name[0]
                if bg.beast is not None:
                    tmp += "B"
            tmp += "\n"
        return tmp



                


class Player(ABC):
    @abstractmethod
    def play(self, board: Board):
        pass

@dataclass
class GameParameters:
    players: List[Player]


class BeastbornGame:

    def __init__(self, parameters: GameParameters):
        self._state = GameState.INIT
        self._parameters = parameters
        self._board = Board()

    def play_game(self):
        while self._state != GameState.END:
            for player in self._parameters.players:
                player.play(self._board)

