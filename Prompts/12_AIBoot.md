# Context
We are enhancing a turn-based strategy game engine (Beastborn). The current bot uses an Alpha-Beta search tree (`BotPlayerController`) which relies on an `AllFindActionStrategy` to generate possible moves. 

I need you to implement a new class `AIFindActionStrategy` using `tinygrad`. This strategy will evaluate and sort candidate moves using a Convolutional Neural Network (CNN) to massively speed up the Alpha-Beta search via move ordering. 

# Requirements

## 1. Network Architecture (tinygrad Mini-ResNet)
Create a `tinygrad` neural network that evaluates the spatial board. We are building an **Afterstate Value Network** using a **Mini-ResNet** architecture (inspired by AlphaZero), NOT a standard flat CNN. 
The network takes a single 3D Tensor `(Batch, Channels, Height, Width)` representing the board state *after* a simulated move, and outputs a single scalar float (the Value/Score).

**Architecture details:**
1. **Initial Stem:** `nn.Conv2d` (e.g., 32 filters, 3x3 kernel, padding=1) -> ReLU.
2. **Residual Body:** 3 to 5 Residual Blocks. Each block consists of:
   - `Conv2d` (32 filters, 3x3, padding=1) -> ReLU -> `Conv2d` (32 filters, 3x3, padding=1)
   - A skip connection adding the block's input to its output before a final ReLU.
3. **Value Head:** A 1x1 `Conv2d` (e.g., 1 or 2 filters) to compress channels -> Flatten -> `nn.Linear` (e.g., 128) -> ReLU -> `nn.Linear` (1) for the final score.

## 2. Rich State Encoding
You must write a helper method `_state_to_tensor(self, gsm, me: int)` that converts the `GameStateMachine`'s current board into a 3D NumPy array of shape `(8, Height, Width)`. Use these specific channels for deep tactical awareness:
- **Channel 0:** Terrain elevation (normalized).
- **Channel 1:** Difficult terrain presence (1.0 if difficult, 0.0 otherwise).
- **Channel 2:** Active player's units presence (1.0).
- **Channel 3:** Active player's units HP percentage (Current HP / Max HP).
- **Channel 4:** Active player's units Energy.
- **Channel 5:** Enemy units presence (1.0).
- **Channel 6:** Enemy units HP percentage.
- **Channel 7:** Enemy units Energy.

## 3. Two Operating Modes
The `AIFindActionStrategy` takes a parameter `mode` (`"WORKING"` or `"LEARNING"`).

### Mode A: Working (Inference/Move Ordering)
- Wrap the base `AllFindActionStrategy` to get all legally possible `Command` objects for the active unit.
- For each possible command, simulate it: `gsm.submit(command)` -> extract the Afterstate via `_state_to_tensor(gsm)` -> `gsm.rollback()`.
- Batch pass all Afterstates through the `tinygrad` ResNet to predict their values.
- Sort the commands based on the predicted value (highest to lowest) and return them.

### Mode B: Learning (Afterstate Reinforcement Learning)
- Implement a Replay Memory (`collections.deque`) with `remember(state_tensor, reward, next_state_tensor, done)`.
- Use the Afterstate Bellman equation: `Target_Value = Reward + gamma * max(Value(next_possible_afterstates))`.
- Implement a `train_step(batch_size)` method that samples from memory.
- Calculate the Mean Squared Error (MSE) loss between the network's predicted Value and `Target_Value`.
- Use `tinygrad`'s auto-differentiation (`loss.backward()`) and `nn.optim.Adam` to update the weights.

## 4. Constraints
- Use pure `tinygrad` and standard Python (`numpy`, `random`, `collections`). **Do NOT use PyTorch.**
- Write clean, type-hinted code fully compatible with the existing `GameStateMachine` and `Command` classes.