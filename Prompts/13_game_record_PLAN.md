# PLAN: Game record format (BGN, "Beastborn Game Notation")

* Create BGN that can be used to fastly save game, or load it.
* This BGN file `train_ai_bot.bgn` should be created from scratch every time training is started.
* Extend train_monitor.py with new tab, so I can select a game, and watch it, implementation of UI should be maximum simple. THe reason to watch games is that I need to see how smart bot is. I like to steer it manually (like move by move, or select autoplay, I can return back or pause if needed.). There should be a score for each player. And clear understanding who win, show player names as well. 

## 1. Questions

| # | Question | Proposed default |
|---|---|---|
| Q1 | How are tiles written? | **Chess-like:** column letter + row number, `a1` = `Position(0, 0)` - compatibile with `_tiles[pos.y][pos.x]` `x` -> `a..p` (columns), `y` -> `1..16` (rows) so similar like with chess|
| Q2 | File extension | **`.bgn`**, UTF-8 text. Several games in one file allowed (separated by new line): handy for bot test sets and training data. |

---

## 2. The format
One row is one game in format:
[game_id][[game_seed][numer_of_players][player_names][game_data]

* game_id - this is a milliseconds when the game was created!
* game_seed - is seed used for RNG used in the game.
* number_of_players - number of players in the game
* player_names - list of name for all players (for training just pass Learner (ai that is under training) and Opponent)
* game_data - must contain each player turn, and moves so when it is given to Interaction as players, the whole game exactly can be reproduced move by move.

## Implementation
GameStateMachine should have optional GameListeners (interface) list passed. So every commit is submitted or unit is moved then it should pass it to the listeners.
Make possible to Interaction take new implementation of PlayerController that is really reading BGN file. Later in train_monitor.py this will be used to view played games. 
Make nice tests for reading / writing BGN formats including play some game, save it and then read it, and compare it with source game.
Keep code clean, do refactoring if needed. Keep comments just focusing how its implemented now and not future or past. 