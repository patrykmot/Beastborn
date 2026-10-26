# This is just some thoughts to understand how AI flow works.


## The idea: score + network
* **Score** = what we know exactly now: `gsm.score(me) - gsm.score(opponent)` after a move.
  * Points: HP the opponent's units have lost (Boss HP counts more), plus a bonus per killed unit.
* **Network** = a guess about the future: how much **more** the score will still change after this move
  (and whether the game will be won). It does not guess the score itself; that is already known.
* Value of a move (`AIFindActionStrategy.rank`):
  * **value = score / AI_SCORE_SCALE + net(board after the move)**
  * A move that wins the game = `+inf` (no network needed).
  * The moves are sorted by value; the best one is played.
* Example: move A hits now (score +3), move B walks closer (score +0).
  * A new network says 0 for both: A wins (only the score counts).
  * A trained network may say B = +0.08 (a big hit 2 turns later): then B wins.
  * That's the job of the network: see what the score cannot see yet.


## From bottom to top flow
*  Training - finding target value for network:
  * `AIFindActionStrategy.targets` -> `return (rewards + self.gamma * following * (1.0 - done))`
    * **target = reward + gamma * net(next board)** (just the reward when the game ended)
    * `rewards` -> `Transition.reward`
      * <- `self.memory` <- `remember` <- `_play` (collects the transitions of a game, remembers them at its end)
      * **reward = (score after my next best move - score after this move) / AI_SCORE_SCALE**
        * `- AI_STEP_COST` every turn (waiting costs a little)
        * `+ AI_PROGRESS_REWARD * how much closer my unit got to the enemy Boss`
        * last move of a finished game: `+ AI_WIN_REWARD` for a win, `- AI_WIN_REWARD` for a loss
        * a stopped game (`--max-turns`) has no last move: it did not really end
    * `following` -> `self.predict(next board)` -> `AfterstateNet(board)`
    * `gamma` (0.95): a reward 1 turn later is worth 95%, 10 turns later ~60%.
    * So: **net(this board) should be = what I get now + (a bit less than) what I get later.**
  * Learning - `train_step`:
    * Take `AI_BATCH_SIZE` random transitions from memory, compute their targets.
    * error = `(net(board) - target)²`, one Adam step makes the network a bit closer to the targets.
    * `_play` does one `train_step` per `AI_TRANSITIONS_PER_TRAIN_STEP` transitions of each game.
    * The loss never goes to 0: the targets use the network too, so they move while it learns.
      Better = the bot wins more against an opponent that does not change.
