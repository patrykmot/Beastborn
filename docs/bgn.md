# Beastborn Game Notation (BGN)

A whole game in one line of text: enough to build the same game again and replay it command by command.
Code: `beastborn/game/bgn.py` (format), `beastborn/game/recorder.py` (recording), `beastborn/control/replay.py`
(a player that plays a record), `beastborn/ui/replay.py` (watching a record forwards and backwards).

```
[1793637382469][42][10x10][2][Learner|Opponent][a1:(b2,-) j10:(xh9,-) b2:c3 c3:xd4 c3:-]
```

## Line

| # | Field | Example | Meaning |
|---|---|---|---|
| 1 | game id | `1793637382469` | milliseconds since the Unix epoch when the game was created |
| 2 | seed | `42` | `new_game(seed=...)`: the map and armies |
| 3 | board | `10x10` | width `x` height |
| 4 | players | `2` | number of players |
| 5 | names | `Learner\|Opponent` | player names in seat order, `\|` between them; `%`, `[`, `]`, `\|` and line breaks are written `%25`, `%5B`, `%5D`, `%7C`, `%0A`, `%0D` |
| 6 | game data | `a1:(b2,-) ...` | the command log, see below |

A `.bgn` file is UTF-8 with any number of games, one per line (empty lines are skipped).
Seed, board size and players are all `new_game` needs, so a record replays only with the same `units.json` and
`constant.py` it was played with. A record that no longer fits the game raises `BgnError` when replayed.

## Tiles

As in chess: column letter + row number. `a1` = `Position(0, 0)`, the top-left tile; x → `a..p`, y → `1..16`.

## Game data

One entry per command of `GameStateMachine.command_log`, separated by spaces. An entry is the tile the active unit
stands on when the command starts, `:`, then the command:

| Notation | Command |
|---|---|
| `c3` | `MoveCommand` to c3 |
| `xd4` | `AttackCommand` on the unit on d4, from where the unit stands |
| `c3xd4` | `AttackCommand` on the unit on d4 after moving to c3 (`move_to`) |
| `-` | `EndTurnCommand` (the unit waits) |
| `(c3xd4,xe5,-)` | a `WrapperCommand` of these commands: a bot plays a whole unit turn as one |

The acting unit is always the GSM's active unit, so the tile before `:` only checks that a replay still follows the
game. Commands are written as logged: an attack that moved first has its `move_to`, a wrapper rejected part-way only
the commands that ran. Turning an entry into a command (`BgnEntry.to_command(gsm)`) needs the game in the state
before it, to find the units on the tiles.

## Use

```python
from beastborn.game import GameRecorder, new_game, read_bgn
from beastborn.game.bgn import append_bgn, replay

gsm = new_game(2, seed=42, width=10, height=10, player_names=["Learner", "Opponent"])
recorder = GameRecorder(gsm)            # listens to the GSM from its first command on
...                                     # play: bots may submit and roll back freely while they search
append_bgn("games.bgn", recorder.record())

record = read_bgn("games.bgn")[0]
end = replay(record)                    # the same game, played to its last entry

from beastborn.control import ReplayController
from beastborn.ui.interaction import Interaction
ui = Interaction(record.new_game(), ReplayController.for_players(record))
while ui.tick():                        # one entry per tick, with log lines and animations as in a live game
    pass
```

`tools/train_ai_bot.py` writes every training game to `train_ai_bot.bgn` (`--bgn`), and the Games tab of
`tools/train_monitor.py` replays them.
