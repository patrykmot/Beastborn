from core import *

def test_board():
    bp: BoardParameters = BoardParameters([Terrain.SNOW, Terrain.DESERT, Terrain.FOREST])
    b: Board = Board(bp)
    board_string = str(b)
    print("Generated board:\n")
    print(board_string)
    assert len(b.psp) == 3
    assert len(board_string) >= BOARD_ROWS * BOARD_COLUMNS