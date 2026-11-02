So what to do with this plato on error? Just idea no code change :)

Checking whether the flat win rate really means no progress
Here are some ideas, roughly in the order I’d try them.

1. Check that it’s really a plateau. This run plays against its own copy from the previous epoch, so a flat ~60% win rate is what you’d see even if the bot improves steadily: each version beats the one before by about the same margin. Self-play numbers can’t show whether it’s getting better overall, so you need a fixed reference:

Play snapshots against each other (epoch 25 vs 50 vs 75 vs 100). If 100 clearly beats 25, it’s still learning.
Play against a fixed opponent (random, or a simple rule-based bot) on maps it hasn’t trained on. Track that win rate over time; it’s the number that shows real progress.
2. The rising loss isn’t necessarily bad. In this kind of training (temporal-difference learning), the loss measures how well the network predicts its own moving targets, not how well it plays. It’s expected to rise when:

more games end with a real win or loss (stopped games went from 29% to 5%), so there are more big ±5 rewards to predict
the opponent gets stronger, so outcomes are less predictable.
It only matters if the predicted values start growing out of control. If they do, the usual fixes are a target network (a separate, slowly updated copy for computing targets), Huber loss and gradient clipping.

3. Check whether the bot can even see the win from early moves. With gamma 0.95 and about 40 bot turns per game, the win reward is multiplied by 0.95^40 ≈ 0.13 by the time it reaches the opening moves. So most of what the bot learns comes from the score changes and the bonus for moving toward the enemy Boss, not from winning. It may be getting good at those intermediate rewards rather than at winning. Ideas:

Raise gamma to 0.98–0.99.
Use n-step or full-game returns, so the final result reaches early moves directly.
Shrink the intermediate rewards over time once the bot has learned the basics.
4. Check whether the bot can only choose between 2 moves. If AI_MAX_RETURNED_COMMANDS = 2 also applies to the learner (not just the opponent), the network only ever picks between the 2 moves your built-in rules rank highest. Then it can never play better than “the better of those two”, which would be a hard limit on progress. I’d check this first, because it’s a ceiling no amount of training can get past.

5. Opponent variety. Playing only against the latest copy can go in circles: it learns to beat version N, forgets what beat version N−5, and so on. Ideas:

Keep a pool of older snapshots and pick an opponent from it each game, plus a random or rule-based player as a fixed anchor.
Let the opponent play greedily most of the time instead of always picking randomly between its top 2, so it’s a tougher, more consistent opponent.
6. Learning settings.

Lower the learning rate once progress stalls (1e-3 → 3e-4 → 1e-4). Plateaus at a fixed learning rate are often just the network bouncing around the best values without settling.
The replay memory holds about 44 epochs of games (200k transitions at about 4.5k per epoch). Much of it is from games against much weaker opponents. A smaller memory, or sampling recent games more often, keeps the training data closer to the bot’s current level.
7. Capacity and data.

3 residual blocks × 32 filters is small. If everything above checks out, try 5–6 blocks or 64 filters.
Fix the seed bug so it sees new maps and plays both sides of each one.
If I had to pick just two: first, make sure the learner isn’t limited to the top 2 moves (4). Then set up a fixed opponent to measure against (1), so you can tell which of the other changes actually helps.


* Quesion:
OK. Problem that I have after some 200 epoch bot (in best case scenarion) will atack eagerly with rat, then with pessant (after rat will day), so its kind of good. But this is the only tactics it can play. How to make tatic learn by bot much advence, like multiple different tactics.  I have idea to use simulated annealing, with high temperature, almost all steps are random, and then bot plays games during training, and temeprerature goes down, so there is less and less random moves, and this "hardening" can be repeated multiple times. The second change could be not to take best result as move every time (during a play), but randomly select move from few best options. Please advice. No code. Just validate my ideas, and give yours as well. 

* **Answer:**
AI Training Strategy Validation & Recommendations
Your ideas address a classic Reinforcement Learning trap: the bot has found a "local optimum" (the rat rush) that yields decent results, and it lacks the structural freedom to discover deeper, multi-unit strategies.
Validating Your Ideas
1. Simulated Annealing (Cyclic Exploration)
Your idea is highly effective and mirrors a proven AI training technique called Cosine Annealing with Warm Restarts.
The Current State: Your setup relies on a fixed 5% chance of taking a random action (`AI_EPSILON = 0.05`).
Why Your Fix Works: By starting with high randomness (high temperature) and slowly hardening it, you force the bot into unseen, complex board states. Repeating the cycle (re-heating) is exactly how you kick a neural network out of a stubborn local optimum like the rat rush.
2. Stochastic Selection (Top-K Sampling)
Choosing randomly from the top 3-5 best options rather than strictly taking the absolute best move is another standard practice.
Why It Works: It prevents the policy from becoming overly deterministic and brittle. This makes the bot less predictable to humans and prevents infinite move-repetition loops during self-play.
---
Additional Strategic Recommendations
1. Reward Shaping
A neural network only optimizes what it is explicitly paid for. If the main rewards are simply winning (`AI_WIN_REWARD`) or stepping toward the enemy boss (`AI_PROGRESS_REWARD`), the bot will naturally just rush forward.
Actionable Step: To teach tactical formations, grant a small immediate reward for ending a turn adjacent to an ally, or apply a slight penalty for ending a turn completely isolated.
2. Historical Self-Play (Opponent Pool)
If the bot only plays against its current self, it only ever learns how to defeat a rat rush.
Actionable Step: Save model snapshots every few dozen epochs and force the active learner to randomly play against these historical versions. This forces the bot to develop robust, generalized tactics to survive varying opponent playstyles.
3. Curriculum Learning (Scenario Training)
Do not exclusively train from the default starting board.
Actionable Step: Programmatically spawn custom training scenarios where units are already grouped, or already positioned defensively on hills. Let the network experience the high mathematical value of a solid defensive formation first; it will then naturally try to recreate that state during a standard game.